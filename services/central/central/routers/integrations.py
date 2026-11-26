"""Entradas de sistemas externos a la central: Lambda, Servientrega, alertas IoT y consulta de Proveedores."""

import hashlib
import time
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from platform_common.auth import ADMIN_BODEGA, ADMIN_GLOBAL, Principal
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from central import external, shipping
from central.config import settings
from central.database import get_db
from central.integrations import KINDS, handle_storage_event
from central.notifications import notify
from central.security import MANAGEMENT, require

router = APIRouter(tags=["Integraciones"])


class StorageEvent(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=120)
    kind: Literal[KINDS]
    object_key: str = Field(..., max_length=300)
    outcome: Literal["OK", "REJECTED"]
    detail: dict[str, Any] = Field(default_factory=dict)


class IotAlert(BaseModel):
    alert_id: int
    warehouse_id: str = Field(..., max_length=20)
    sensor_id: str = Field(..., max_length=40)
    sensor_type: str = Field(..., max_length=20)
    rule: str = Field(..., max_length=40)
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    message: str = Field(..., max_length=500)
    opened_at: datetime

    def key(self) -> str:
        # Una sola alerta abierta por sensor y regla: sensor + regla + apertura la identifican aunque la base IoT se reinicie
        return hashlib.sha256(f"{self.sensor_id}|{self.rule}|{self.opened_at.isoformat()}".encode()).hexdigest()[:24]


class ShipmentUpdate(BaseModel):
    tracking_number: str = Field(..., max_length=40)
    reference: str = Field(..., max_length=30)
    status: Literal["CREATED", "IN_TRANSIT", "DELIVERED", "RETURNED"]
    detail: Optional[str] = Field(None, max_length=300)


async def _signed_body(request: Request, secret: str) -> bytes:
    if not secret:
        raise HTTPException(status_code=503, detail="Integración no configurada")
    body = await request.body()
    if not external.valid_signature(secret, request.headers.get("X-Signature-Timestamp"),
                                    request.headers.get("X-Signature"), body, time.time()):
        raise HTTPException(status_code=401, detail="Firma inválida o vencida")
    return body


@router.post("/integrations/storage-events", include_in_schema=True, summary="Aviso firmado de la Lambda de S3")
async def storage_event(request: Request, db: Session = Depends(get_db)):
    body = await _signed_body(request, settings.LAMBDA_SHARED_SECRET)
    try:
        event = StorageEvent.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False)) from None
    result = handle_storage_event(db, event.model_dump())
    db.commit()
    return {"event_id": event.event_id, "result": result}


@router.post("/integrations/servientrega", summary="Webhook firmado de Servientrega (cambios de estado del envío)")
async def servientrega_webhook(request: Request, db: Session = Depends(get_db)):
    body = await _signed_body(request, settings.SERVIENTREGA_WEBHOOK_SECRET)
    try:
        update = ShipmentUpdate.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False)) from None
    result = shipping.apply_status(db, update.reference, update.tracking_number, update.status, update.detail, "webhook")
    db.commit()
    return {"reference": update.reference, "result": result}


@router.post("/integrations/iot-alerts", summary="Alerta firmada de un sensor (llega al administrador de la bodega)")
async def iot_alert(request: Request, db: Session = Depends(get_db)):
    body = await _signed_body(request, settings.IOT_SHARED_SECRET)
    try:
        alert = IotAlert.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False)) from None
    title = f"{alert.warehouse_id}: {alert.message}"[:200]
    created = [notify(db, role, "IOT_ALERT", title, f"Sensor {alert.sensor_id} ({alert.rule})", alert.severity,
                      warehouse_id=alert.warehouse_id, dedupe_key=f"iot:{alert.key()}:{role}") for role in (ADMIN_BODEGA, ADMIN_GLOBAL)]
    db.commit()
    return {"alert_id": alert.alert_id, "result": "applied" if any(created) else "duplicate"}


@router.get("/suppliers/{nit}", summary="Consultar un proveedor en el sistema de Proveedores (síncrono)")
def supplier(nit: str, _: Principal = Depends(require(*MANAGEMENT))):
    try:
        found = external.get_supplier(nit)
    except external.ExternalUnavailable as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    if found is None:
        raise HTTPException(status_code=404, detail="El sistema de Proveedores no conoce ese NIT")
    return {"nit": found.nit, "name": found.name, "status": found.status, "email": found.email}
