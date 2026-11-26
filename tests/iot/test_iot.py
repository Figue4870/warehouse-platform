"""Fase 12: duplicados, fuera de orden, buffering, idempotencia y alertas de la extensión IoT."""

import json
import socket
import struct
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from iot_service import api as iot_api
from iot_service import models
from iot_service.gateway import Buffer, Dedupe, Gateway
from iot_service.mqtt import MqttClient
from iot_service.workers import aggregate, check_alerts, store


# gateway de borde
@pytest.fixture
def gateway(tmp_path):
    sent, state = [], {"down": False}

    def post(batch):
        if state["down"]:
            raise ConnectionError("ingesta caída")
        sent.append(batch)

    gw = Gateway(Buffer(str(tmp_path / "buffer.sqlite3")), post, "gw-prueba")
    gw.sent, gw.state = sent, state
    return gw


def test_el_gateway_quita_duplicados_y_marca_atrasadas(gateway, sensor):
    temp = sensor()
    first, second, third = temp(21), temp(21.5), temp(22)
    for reading in (first, third, second, third):
        gateway.receive(reading.model_dump_json().encode())
    gateway.flush(now=1e9)
    forwarded = [r for batch in gateway.sent for r in batch["readings"]]
    assert [r["sequence_number"] for r in forwarded] == [1, 3, 2]
    assert [r["late"] for r in forwarded] == [False, False, True]
    assert gateway.stats["duplicates_dropped"] == 1


def test_sin_ingesta_el_buffer_guarda_y_luego_entrega_en_orden(gateway, sensor):
    temp = sensor()
    gateway.state["down"] = True
    for value in range(5):
        gateway.receive(temp(20 + value).model_dump_json().encode())
    assert gateway.flush(now=1e9) == 0 and gateway.buffer.size() == 5
    gateway.state["down"] = False
    assert gateway.flush(now=1e9 + 60) == 5 and gateway.buffer.size() == 0
    assert [r["sequence_number"] for r in gateway.sent[0]["readings"]] == [1, 2, 3, 4, 5]


def test_el_buffer_sobrevive_a_un_reinicio(tmp_path, sensor):
    path = str(tmp_path / "b.sqlite3")
    Gateway(Buffer(path), lambda b: (_ for _ in ()).throw(ConnectionError()), "gw").receive(sensor()(20).model_dump_json().encode())
    assert Buffer(path).size() == 1


def test_lectura_invalida_se_cuenta_y_no_se_reenvia(gateway):
    assert gateway.receive(b'{"sensor_id": "X"}') == "invalid"
    assert gateway.buffer.size() == 0


def test_la_ventana_de_duplicados_es_acotada():
    dedupe = Dedupe(window=3)
    for seq in range(1, 6):
        dedupe.check("S", seq)
    assert len(dedupe.seen["S"]) == 3


# almacenamiento
def test_guardar_es_idempotente_y_el_estado_solo_avanza(iot_sf, sensor):
    temp = sensor()
    r1, r2, r3 = temp(20), temp(21), temp(22)
    with iot_sf() as db:
        assert store(db, r1) == "stored"
        assert store(db, r3) == "stored"           # llegó antes la 3
        assert store(db, r2) == "late"             # la 2 llega tarde
        assert store(db, r2) == "duplicate"
        state = db.get(models.SensorStateEntity, "TEMP-001-001")
        assert (state.last_sequence, state.last_value, state.readings, state.late_readings, state.gaps) == (3, 22, 3, 1, 0)
        assert db.query(models.ReadingEntity).count() == 3


# alertas
def test_temperatura_fuera_de_rango_abre_y_cierra_alerta(iot_sf, sensor):
    temp, notified = sensor(), []
    with iot_sf() as db:
        results = [check_alerts(db, temp(v), lambda a: notified.append(a.rule) or True) for v in (21, 35, 36, 37, 36, 22)]
        assert results == ["normal", "normal", "normal", "opened", "normal", "closed"]
        assert notified == ["TEMPERATURE_OUT_OF_RANGE"]
        alert = db.query(models.IotAlertEntity).one()
        assert alert.closed_at is not None and alert.notified_at is not None


def test_las_reglas_ignoran_lecturas_repetidas_o_atrasadas(iot_sf, sensor):
    temp = sensor()
    readings = [temp(35) for _ in range(3)]
    with iot_sf() as db:
        check_alerts(db, readings[0], lambda a: True)
        check_alerts(db, readings[2], lambda a: True)
        assert check_alerts(db, readings[1], lambda a: True) == "skipped"
        assert check_alerts(db, readings[2], lambda a: True) == "skipped"
        assert db.query(models.IotAlertEntity).count() == 0   # solo 2 seguidas en orden: aún no confirma


def test_puerta_abierta_demasiado_tiempo(iot_sf, sensor, monkeypatch):
    monkeypatch.setenv("IOT_DOOR_OPEN_SECONDS", "60")
    door = sensor("door", "DOOR-001-015")
    with iot_sf() as db:
        results = [check_alerts(db, door(1, seconds=30), lambda a: True) for _ in range(4)]
        assert results == ["normal", "normal", "normal", "opened"]      # abierta 0, 30, 60 y 90 s
        assert check_alerts(db, door(0), lambda a: True) == "closed"


def test_si_la_central_no_responde_la_alerta_se_reintenta(iot_sf, sensor):
    temp, central = sensor(), {"up": False}
    notifier = lambda alert: central["up"]  # noqa: E731
    with iot_sf() as db:
        for v in (35, 36, 37):
            check_alerts(db, temp(v), notifier)
        assert db.query(models.IotAlertEntity).one().notified_at is None
        central["up"] = True
        check_alerts(db, temp(36), notifier)
        assert db.query(models.IotAlertEntity).one().notified_at is not None


# analítica
def test_los_agregados_no_cuentan_dos_veces(iot_sf, sensor):
    temp = sensor()
    r1, r2 = temp(20), temp(24)
    with iot_sf() as db:
        now = r2.timestamp + timedelta(milliseconds=300)
        assert [aggregate(db, r, now) for r in (r1, r2, r1)] == ["aggregated", "aggregated", "duplicate"]
        stat = db.query(models.MinuteStatEntity).one()
        assert (stat.readings, stat.value_min, stat.value_max, stat.value_sum) == (2, 20, 24, 44)


# ingesta
class FakePublisher:
    def __init__(self):
        self.messages, self.down = [], False

    def publish(self, **kwargs):
        if self.down:
            raise ConnectionError("RabbitMQ caído")
        self.messages.append(kwargs)


@pytest.fixture
def ingestion(iot_sf, monkeypatch):
    fake = FakePublisher()
    monkeypatch.setattr(iot_api, "publisher", lambda: fake)

    def _db():
        db = iot_sf()
        try:
            yield db
        finally:
            db.close()

    iot_api.app.dependency_overrides[iot_api.get_db] = _db
    with TestClient(iot_api.app) as client:
        client.fake = fake
        yield client
    iot_api.app.dependency_overrides.clear()


def test_ingesta_publica_las_validas_y_rechaza_las_demas(ingestion, sensor):
    temp = sensor()
    readings = [json.loads(temp(21).model_dump_json()), {"event_id": "x", "sensor_type": "lava"}]
    response = ingestion.post("/ingest", json={"gateway_id": "gw", "readings": readings})
    assert response.status_code == 202 and response.json()["accepted"] == 1 and response.json()["rejected"] == 1
    assert ingestion.fake.messages[0]["routing_key"] == "reading.temperature.WH-001"


def test_si_rabbitmq_cae_la_ingesta_responde_503_y_el_gateway_reintenta(ingestion, sensor):
    ingestion.fake.down = True
    response = ingestion.post("/ingest", json={"gateway_id": "gw", "readings": [json.loads(sensor()(21).model_dump_json())]})
    assert response.status_code == 503


def test_resumen_y_experimento(ingestion, iot_sf, sensor):
    temp, door = sensor(), sensor("door", "DOOR-002-015", "WH-002")
    with iot_sf() as db:
        for reading in (temp(21), temp(22), door(0)):
            store(db, reading)
    summary = ingestion.get("/iot/summary", params={"minutes": 1440}).json()
    assert [w["warehouse_id"] for w in summary["warehouses"]] == ["WH-001", "WH-002"]
    assert summary["throughput_per_second"] == 0.05   # 3 lecturas guardadas en el último minuto
    result = ingestion.get("/iot/experiment", params={"since": "2026-11-20T00:00:00Z"}).json()
    assert result["stored"] == 3 and result["per_warehouse"] == {"WH-001": 2, "WH-002": 1}


# MQTT
def fake_broker(received):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        conn.recv(1024)
        conn.sendall(b"\x20\x02\x00\x00")                      # CONNACK aceptado
        data = conn.recv(4096)
        topic_len = struct.unpack("!H", data[2:4])[0]
        packet_id = data[4 + topic_len:6 + topic_len]
        received.append((data[4:4 + topic_len].decode(), data[6 + topic_len:]))
        conn.sendall(b"\x40\x02" + packet_id)                  # PUBACK
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def test_cliente_mqtt_publica_con_qos1():
    received = []
    client = MqttClient("127.0.0.1", fake_broker(received), "sensor-1", "iot", "iot")
    client.connect()
    client.publish("sensors/WH-001/temperature/TEMP-001-001", b'{"v": 1}')
    client.close()
    assert received == [("sensors/WH-001/temperature/TEMP-001-001", b'{"v": 1}')]


def test_movimiento_nocturno_abre_una_sola_alerta_hasta_que_se_calma(iot_sf, sensor, monkeypatch):
    monkeypatch.setenv("IOT_MOTION_QUIET_SECONDS", "60")
    motion = sensor("motion", "MOV-001-011")
    motion.clock = motion.clock.replace(hour=5)  # 00:00 en Bogotá
    with iot_sf() as db:
        results = [check_alerts(db, motion(v, seconds=10), lambda a: True) for v in (1, 0, 1, 0, 0)]
        assert results == ["opened", "normal", "normal", "normal", "normal"]   # detecciones cercanas: la misma alerta
        assert check_alerts(db, motion(0, seconds=60), lambda a: True) == "closed"  # un minuto sin movimiento
        assert db.query(models.IotAlertEntity).count() == 1


def test_movimiento_en_horario_de_operacion_no_alerta(iot_sf, sensor):
    motion = sensor("motion", "MOV-001-011")  # 10:00 en Bogotá
    with iot_sf() as db:
        assert [check_alerts(db, motion(1), lambda a: True) for _ in range(3)] == ["normal"] * 3
