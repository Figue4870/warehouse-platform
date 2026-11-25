"""Lado bodega: POST /sync/heartbeat responde cifrado solo a quien tiene la clave."""

import pytest
from platform_common import secure_heartbeat as hb

from app.config import settings
from app.modules.sync import heartbeat as warehouse_heartbeat
from app.workers.relay import OutboxRelay

SECRET = "clave-de-prueba-de-la-bodega-123456"
KEY = hb.derive_key(SECRET)


class RecordingPublisher:
    def publish(self, **kwargs):
        pass


@pytest.fixture(autouse=True)
def heartbeat_secret(monkeypatch):
    monkeypatch.setattr(settings, "HEARTBEAT_SECRET", SECRET)
    monkeypatch.setattr(warehouse_heartbeat, "replay_guard", hb.ReplayGuard())


def challenge_for(key=KEY, warehouse_id="WH-TEST"):
    challenge = hb.new_challenge(warehouse_id)
    return challenge, hb.seal(key, hb.CHALLENGE, warehouse_id, challenge)


def test_responde_cifrado_con_el_reto_y_su_estado(client, create_product, move, session_factory):
    product = create_product(sku="HB-1")
    move(product["id"], "RECEIPT", 5)
    OutboxRelay(session_factory, RecordingPublisher()).run_once()
    move(product["id"], "SHIPMENT", 1)

    challenge, envelope = challenge_for()
    response = client.post("/sync/heartbeat", json=envelope)
    assert response.status_code == 200
    assert "last_sequence" not in response.text
    status = hb.verify_response(KEY, "WH-TEST", challenge, response.json())
    assert status["last_sequence"] == 2 and status["outbox_pending"] == 1
    assert status["instance"] == settings.INSTANCE_NAME


def test_una_central_con_otra_clave_recibe_401_sin_detalles(client):
    _, envelope = challenge_for(key=hb.derive_key("otra-clave-que-no-es-la-buena-000"))
    response = client.post("/sync/heartbeat", json=envelope)
    assert response.status_code == 401
    assert response.json()["detail"] == "Reto de heartbeat no válido"


def test_un_reto_repetido_recibe_409(client):
    _, envelope = challenge_for()
    assert client.post("/sync/heartbeat", json=envelope).status_code == 200
    assert client.post("/sync/heartbeat", json=envelope).status_code == 409


def test_un_reto_para_otra_bodega_se_rechaza(client):
    _, envelope = challenge_for(warehouse_id="WH-999")
    assert client.post("/sync/heartbeat", json=envelope).status_code == 401


def test_basura_en_lugar_del_reto(client):
    assert client.post("/sync/heartbeat", json={"hola": "mundo"}).status_code == 401


def test_sin_clave_configurada_no_responde(client, monkeypatch):
    monkeypatch.setattr(settings, "HEARTBEAT_SECRET", None)
    _, envelope = challenge_for()
    response = client.post("/sync/heartbeat", json=envelope)
    assert response.status_code == 503 and "HEARTBEAT_SECRET" in response.json()["detail"]


def test_la_bodega_no_envia_heartbeats_por_su_cuenta():
    assert not hasattr(OutboxRelay, "send_heartbeat")
    assert not hasattr(settings, "HEARTBEAT_SECONDS")
