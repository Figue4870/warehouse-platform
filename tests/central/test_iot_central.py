"""Fase 12 en la central: alertas firmadas de sensores y consulta al servicio IoT."""

import json
import time

from platform_common.auth import ADMIN_BODEGA

from central import external
from central.config import settings
from central.models import NotificationEntity


def signed(client, payload, secret):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    return client.post("/integrations/iot-alerts", content=body, headers={
        "Content-Type": "application/json", "X-Signature-Timestamp": ts, "X-Signature": external.signature(secret, ts, body)})


def test_alerta_de_sensor_llega_al_administrador_de_la_bodega(central_client, central_sf, monkeypatch):
    monkeypatch.setattr(settings, "IOT_SHARED_SECRET", "iot-secreto")
    alert = {"alert_id": 7, "warehouse_id": "WH-001", "sensor_id": "TEMP-001-001", "sensor_type": "temperature",
             "rule": "TEMPERATURE_OUT_OF_RANGE", "severity": "CRITICAL", "message": "Temperatura fuera del rango permitido",
             "opened_at": "2026-11-20T15:00:06+00:00"}
    assert signed(central_client, alert, "iot-secreto").json()["result"] == "applied"
    assert signed(central_client, alert, "iot-secreto").json()["result"] == "duplicate"
    assert signed(central_client, alert, "otra").status_code == 401
    with central_sf() as db:
        rows = db.query(NotificationEntity).filter(NotificationEntity.kind == "IOT_ALERT").all()
        assert {(n.audience_role, n.warehouse_id) for n in rows} == {(ADMIN_BODEGA, "WH-001"), ("ADMIN_GLOBAL", "WH-001")}


def test_otra_alerta_con_el_mismo_id_no_se_confunde(central_client, central_sf, monkeypatch):
    # Si la base IoT se reinicia, los id vuelven a empezar; la alerta se identifica por sensor, regla y apertura
    monkeypatch.setattr(settings, "IOT_SHARED_SECRET", "iot-secreto")
    base = {"alert_id": 1, "warehouse_id": "WH-002", "sensor_id": "HUM-002-007", "sensor_type": "humidity",
            "rule": "HUMIDITY_HIGH", "severity": "WARNING", "message": "Humedad alta"}
    assert signed(central_client, {**base, "opened_at": "2026-11-20T15:00:00+00:00"}, "iot-secreto").json()["result"] == "applied"
    assert signed(central_client, {**base, "opened_at": "2026-11-21T09:30:00+00:00"}, "iot-secreto").json()["result"] == "applied"
    with central_sf() as db:
        assert db.query(NotificationEntity).filter(NotificationEntity.warehouse_id == "WH-002",
                                                   NotificationEntity.kind == "IOT_ALERT").count() == 4


def test_sin_servicio_iot_la_central_responde_503(central_client, monkeypatch):
    monkeypatch.setattr(settings, "IOT_API_URL", "http://127.0.0.1:9")
    response = central_client.get("/iot/summary")
    assert response.status_code == 503 and "IoT" in response.json()["detail"]
