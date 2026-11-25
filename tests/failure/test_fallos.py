"""Matriz de fallos de la Fase 10 contra la plataforma levantada (ver docs/fases/fase-10-resiliencia.md)."""

import json
import os
import re
import time
import uuid

import httpx
import pika
import pytest
from it_helpers import wait_until

WH_AMQP_URL = os.getenv("WH_AMQP_URL", "amqp://warehouse:warehouse@localhost:5673/%2F")
CENTRAL_AMQP_URL = os.getenv("CENTRAL_AMQP_URL", "amqp://platform:platform@localhost:5680/%2F")
MOCKS_URL = os.getenv("EXTERNAL_MOCKS_URL", "http://localhost:8099")


def queue_depth(api, queue: str) -> int:
    text = api.get("/metrics/prometheus").text
    match = re.search(rf'^rabbitmq_queue_messages\{{kind="[a-z]+",queue="{re.escape(queue)}"\}} (\S+)', text, re.M)
    return int(float(match.group(1))) if match else 0


def publish(url: str, exchange: str, routing_key: str, body: bytes, message_id: str | None = None):
    connection = pika.BlockingConnection(pika.URLParameters(url))
    try:
        channel = connection.channel()
        channel.confirm_delivery()
        channel.basic_publish(exchange, routing_key, body, pika.BasicProperties(
            delivery_mode=2, content_type="application/json", message_id=message_id))
    finally:
        connection.close()


def stock(api, product_id):
    return api.get(f"/inventory/{product_id}").json()["quantity"]


def test_redis_caido_la_bodega_responde_desde_postgresql(api, product, chaos):
    chaos.stop("redis")
    response = api.get(f"/inventory/{product['id']}")
    assert response.status_code == 200 and response.headers["X-Cache"] == "BYPASS"
    assert api.post("/inventory/movements", json={"product_id": product["id"], "type": "SHIPMENT", "quantity": 1}).status_code == 201
    assert api.get("/health").json()["redis"] == "down"
    chaos.start("redis")
    wait_until(lambda: api.get(f"/inventory/{product['id']}").headers["X-Cache"] in ("MISS", "HIT"), what="que Redis vuelva")
    assert stock(api, product["id"]) == 19


def test_rabbitmq_caido_los_eventos_esperan_en_el_outbox(api, central, product, chaos):
    chaos.stop("rabbitmq")
    assert api.post("/inventory/movements", json={"product_id": product["id"], "type": "SHIPMENT", "quantity": 5}).status_code == 201
    assert wait_until(lambda: api.get("/metrics").json()["outbox"]["pending"] >= 1, timeout=20, what="el evento en el outbox")
    chaos.start("rabbitmq")
    wait_until(lambda: api.get("/metrics").json()["outbox"]["pending"] == 0, timeout=120, what="que el relay vacíe el outbox")
    wait_until(lambda: central.get(f"/global/inventory/{product['sku']}").json()["total_quantity"] == 15, timeout=120,
               what="que la central reciba el movimiento")


def test_central_caida_la_bodega_sigue_operando(api, central, product, chaos):
    chaos.stop("central")
    for _ in range(3):
        assert api.post("/inventory/movements", json={"product_id": product["id"], "type": "SHIPMENT", "quantity": 1}).status_code == 201
    assert stock(api, product["id"]) == 17
    chaos.start("central")
    wait_until(lambda: central.get(f"/global/inventory/{product['sku']}").json()["total_quantity"] == 17, timeout=180,
               what="que la central se ponga al día")


def test_worker_caido_los_mensajes_esperan_en_la_cola(api, product, chaos):
    chaos.stop("notification_worker")
    api.post("/inventory/movements", json={"product_id": product["id"], "type": "SHIPMENT", "quantity": 20})
    wait_until(lambda: queue_depth(api, "wh.notifications") >= 1, timeout=30, what="el aviso de agotado en la cola")
    alert = next(a for a in api.get("/alerts", params={"product_id": product["id"]}).json() if a["type"] == "OUT_OF_STOCK")
    assert alert["notified_at"] is None
    chaos.start("notification_worker")
    wait_until(lambda: next(a for a in api.get("/alerts", params={"product_id": product["id"]}).json()
                            if a["type"] == "OUT_OF_STOCK")["notified_at"], timeout=60, what="que el worker procese el aviso")


def test_mensaje_invalido_va_directo_a_la_dlq(api):
    before = queue_depth(api, "wh.notifications.dlq")
    publish(WH_AMQP_URL, "warehouse.events", "stock.depleted", b"{esto no es json", f"bad-{uuid.uuid4()}")
    wait_until(lambda: queue_depth(api, "wh.notifications.dlq") == before + 1, timeout=30, what="el mensaje en la DLQ")


def test_evento_repetido_se_aplica_una_sola_vez(api, central, product):
    sku = product["sku"]
    wait_until(lambda: central.get(f"/global/inventory/{sku}").json()["total_quantity"] == 20, what="el saldo en la central")
    events = central_events_for(api, sku)
    event = events[-1]
    body = json.dumps(event).encode()
    for _ in range(3):
        routing_key = f"{event['warehouse_id']}.{event['event_type'].lower().replace('_', '.')}"
        publish(CENTRAL_AMQP_URL, "central.events", routing_key, body, event["event_id"])
    time.sleep(5)
    assert central.get(f"/global/inventory/{sku}").json()["total_quantity"] == 20


def test_proveedores_caido_el_contrato_sigue_pendiente(mocks):
    import importlib.util
    from pathlib import Path

    from it_helpers import CENTRAL_URL, client

    path = Path(__file__).resolve().parents[2] / "scripts" / "contratos" / "generar_contrato.py"
    spec = importlib.util.spec_from_file_location("generar_contrato", path)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    pdf = generator.build_pdf(generator.contract_lines("900123456-7", "Seguridad Industrial del Valle", "2026-11-01", None,
                                                       [("EPP-CASCO-001", "17000")]))
    with client(CENTRAL_URL, "lider") as lider:
        contract = lider.post("/contracts", data={"title": f"Prueba de fallos {uuid.uuid4().hex[:6]}"},
                              files={"file": ("c.pdf", pdf, "application/pdf")}).json()
        cid = contract["contract_id"]
        wait_until(lambda: lider.get(f"/contracts/{cid}").json()["status"] == "EXTRACTED", timeout=90,
                   what="que la Lambda lea el contrato")
        mocks(suppliers={"down": True})
        response = lider.post(f"/contracts/{cid}/approve", json={"apply_prices": False})
        assert response.status_code == 503 and lider.get(f"/contracts/{cid}").json()["status"] == "EXTRACTED"
        mocks(suppliers={"down": False})
        assert lider.post(f"/contracts/{cid}/approve", json={"apply_prices": False}).json()["status"] == "APPROVED"


def central_events_for(api, sku):
    with httpx.Client(base_url=str(api.base_url), headers=central_auth(), timeout=10) as admin:
        data = admin.get("/sync/events", params={"after_sequence": 0, "limit": 2000}).json()
    return [e for e in data["events"] if e["payload"].get("sku") == sku]


def central_auth():
    from it_helpers import ADMIN_GLOBAL, auth

    return auth(ADMIN_GLOBAL)


@pytest.fixture
def mocks():
    try:
        httpx.get(f"{MOCKS_URL}/health", timeout=3)
    except httpx.HTTPError:
        pytest.skip(f"Los sistemas externos simulados no responden en {MOCKS_URL}")
    yield lambda **control: httpx.post(f"{MOCKS_URL}/_control", json=control, timeout=5)
    httpx.post(f"{MOCKS_URL}/_control", json={"suppliers": {"down": False}, "servientrega": {"down": False, "webhook": True}}, timeout=5)


def test_servientrega_caida_la_transferencia_espera_y_luego_sigue(api, central, product, mocks):
    mocks(servientrega={"down": True})
    transfer = central.post("/transfers", json={"sku": product["sku"], "source_warehouse_id": "WH-001",
                                                "destination_warehouse_id": "WH-002", "quantity": 2}).json()
    tid = transfer["transfer_id"]
    detail = wait_until(lambda: (t := central.get(f"/transfers/{tid}").json())["shipment"] and t["shipment"]["attempts"] >= 1 and t,
                        timeout=60, what="el primer intento con Servientrega")
    assert detail["state"] == "SHIPPING" and "503" in (detail["shipment"]["last_error"] or "")
    mocks(servientrega={"down": False})
    done = wait_until(lambda: (t := central.get(f"/transfers/{tid}").json())["state"] in ("COMPLETED", "RESERVATION_RELEASED") and t,
                      timeout=180, what="que la transferencia termine")
    assert done["state"] == "COMPLETED"
