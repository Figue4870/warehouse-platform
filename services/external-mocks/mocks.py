"""Sistemas externos simulados del diagrama de contexto.

/proveedores   API síncrona de proveedores (consulta por NIT).
/servientrega  API asíncrona de envíos: acepta el pedido (202) y avisa los cambios de estado por webhook firmado.
/_control      Activa fallas para las pruebas de resiliencia (caída, latencia, devoluciones, webhook perdido).

El estado vive en memoria: es un simulador, no un sistema de registro.
"""

import hashlib
import hmac
import itertools
import json
import os
import threading
import time
import urllib.request
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

app = FastAPI(title="Sistemas externos simulados", version="1.0.0")

SUPPLIERS = {
    "900123456-7": ("Seguridad Industrial del Valle", "ACTIVE", "ventas@seguridadvalle.example"),
    "900234567-1": ("Distribuciones Médicas SAS", "ACTIVE", "pedidos@dimedicas.example"),
    "800555111-2": ("Ferretería Mayorista Andina", "ACTIVE", "comercial@ferreandina.example"),
    "901222333-4": ("Empaques del Pacífico", "ACTIVE", "ventas@empaquespacifico.example"),
    "830444555-6": ("Plásticos Industriales Cali", "ACTIVE", "ventas@plasticoscali.example"),
    "860999888-0": ("Suministros del Norte Ltda", "SUSPENDED", None),
}

control = {
    "suppliers": {"down": False, "latency_ms": int(os.environ.get("SUPPLIERS_LATENCY_MS", "40"))},
    "servientrega": {"down": False, "deliver_after_seconds": int(os.environ.get("SERVIENTREGA_DELIVER_AFTER_SECONDS", "8")),
                     "return_next": False, "webhook": True},
}
shipments: dict[str, dict] = {}
by_reference: dict[str, str] = {}
lock = threading.Lock()
numbers = itertools.count(1)


class ControlUpdate(BaseModel):
    suppliers: Optional[dict] = None
    servientrega: Optional[dict] = None


class ShipmentRequest(BaseModel):
    reference: str = Field(..., max_length=30)
    origin: dict
    destination: dict
    items: list[dict]
    callback_url: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/_control")
def get_control():
    return control


@app.post("/_control")
def set_control(update: ControlUpdate):
    for name in ("suppliers", "servientrega"):
        values = getattr(update, name)
        if values:
            control[name].update({k: v for k, v in values.items() if k in control[name]})
    return control


# Proveedores (síncrono)
@app.get("/proveedores/suppliers/{nit}")
def get_supplier(nit: str):
    if control["suppliers"]["down"]:
        raise HTTPException(status_code=503, detail="Proveedores fuera de servicio")
    time.sleep(control["suppliers"]["latency_ms"] / 1000)
    if nit not in SUPPLIERS:
        raise HTTPException(status_code=404, detail="Proveedor no registrado")
    name, state, email = SUPPLIERS[nit]
    return {"nit": nit, "name": name, "status": state, "email": email}


@app.get("/proveedores/suppliers")
def list_suppliers():
    return [{"nit": nit, "name": name, "status": state} for nit, (name, state, _) in SUPPLIERS.items()]


# Servientrega (asíncrono)
def _sign(body: bytes) -> dict:
    secret = os.environ.get("SERVIENTREGA_WEBHOOK_SECRET", "")
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Signature-Timestamp": timestamp, "X-Signature": signature}


def _advance(tracking: str, new_status: str, detail: str | None = None) -> None:
    with lock:
        shipment = shipments[tracking]
        shipment["status"], shipment["detail"] = new_status, detail
        shipment["history"].append({"status": new_status, "at": time.time()})
    if shipment.get("callback_url") and control["servientrega"]["webhook"]:
        body = json.dumps({"tracking_number": tracking, "reference": shipment["reference"], "status": new_status,
                           "detail": detail}).encode()
        request = urllib.request.Request(shipment["callback_url"], data=body, method="POST", headers=_sign(body))
        try:
            urllib.request.urlopen(request, timeout=3).close()
        except OSError:
            pass  # el cliente consulta el estado si el aviso no le llegó


def _deliver(tracking: str, returned: bool) -> None:
    wait = control["servientrega"]["deliver_after_seconds"]
    time.sleep(wait / 2)
    _advance(tracking, "IN_TRANSIT")
    time.sleep(wait / 2)
    _advance(tracking, "RETURNED" if returned else "DELIVERED", "Destinatario no recibió" if returned else None)


@app.post("/servientrega/shipments", status_code=status.HTTP_202_ACCEPTED)
def create_shipment(body: ShipmentRequest, idempotency_key: Optional[str] = Header(None)):
    if control["servientrega"]["down"]:
        raise HTTPException(status_code=503, detail="Servientrega fuera de servicio")
    key = idempotency_key or body.reference
    with lock:
        if key in by_reference:
            return shipments[by_reference[key]]
        tracking = f"SRV{next(numbers):09d}"
        returned = control["servientrega"]["return_next"]
        control["servientrega"]["return_next"] = False
        shipments[tracking] = {"tracking_number": tracking, "reference": body.reference, "status": "CREATED",
                               "detail": None, "callback_url": body.callback_url, "items": body.items,
                               "history": [{"status": "CREATED", "at": time.time()}]}
        by_reference[key] = tracking
    threading.Thread(target=_deliver, args=(tracking, returned), daemon=True).start()
    return {k: v for k, v in shipments[tracking].items() if k != "callback_url"}


@app.get("/servientrega/shipments/{tracking}")
def get_shipment(tracking: str):
    if control["servientrega"]["down"]:
        raise HTTPException(status_code=503, detail="Servientrega fuera de servicio")
    if tracking not in shipments:
        raise HTTPException(status_code=404, detail="Guía no encontrada")
    return {k: v for k, v in shipments[tracking].items() if k != "callback_url"}
