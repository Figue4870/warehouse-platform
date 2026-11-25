"""Lado central: solo se aceptan respuestas auténticas (bodegas simuladas con httpx.MockTransport)."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from platform_common import secure_heartbeat as hb

from central import heartbeat as central_heartbeat
from central.config import settings, warehouse_registry
from central.heartbeat import NO_SECRET, OK, REJECTED, UNREACHABLE, HeartbeatClient, poll_once, poll_warehouse
from central.models import WarehouseEntity
from central.status import warehouse_status

SECRET = "clave-de-prueba-wh001-abcdefghijk"
KEY = hb.derive_key(SECRET)
URL = "http://wh-001"


def real_warehouse(key=KEY, warehouse_id="WH-001", last_sequence=7, instance="WH-001-api-3"):
    """Bodega simulada que responde igual que la API real, con la clave `key`."""
    guard = hb.ReplayGuard()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sync/heartbeat"
        try:
            answer = hb.answer_challenge(key, warehouse_id, json.loads(request.content),
                                         {"last_sequence": last_sequence, "outbox_pending": 0, "instance": instance}, guard)
        except hb.ReplayError:
            return httpx.Response(409)
        except hb.HeartbeatError:
            return httpx.Response(401, json={"detail": "Reto de heartbeat no válido"})
        return httpx.Response(200, json=answer)

    return handler


def client_for(handler) -> HeartbeatClient:
    return HeartbeatClient(transport=httpx.MockTransport(handler))


def poll(central_sf, handler, secret=SECRET):
    return poll_warehouse(central_sf, "WH-001", URL, secret, client_for(handler))


def row(central_sf) -> WarehouseEntity:
    with central_sf() as db:
        warehouse = db.get(WarehouseEntity, "WH-001")
        db.expunge(warehouse)
        return warehouse


def test_bodega_verdadera_queda_online_y_reporta_su_estado(central_sf):
    result = poll(central_sf, real_warehouse())
    assert result.outcome == OK and result.instance == "WH-001-api-3"
    wh = row(central_sf)
    assert wh.last_heartbeat_at is not None and wh.reported_sequence == 7 and wh.api_url == URL
    assert wh.last_heartbeat_instance == "WH-001-api-3" and wh.heartbeat_failures == 0
    status = warehouse_status(wh, datetime.now(timezone.utc))
    assert status["status"] == "ONLINE" and status["heartbeat"]["authenticated"] is True


def test_bodega_falsa_sin_la_clave_es_rechazada(central_sf):
    result = poll(central_sf, real_warehouse(key=hb.derive_key("clave-del-impostor-000000000")))
    assert result.outcome == REJECTED
    wh = row(central_sf)
    assert wh.last_heartbeat_at is None and wh.heartbeat_rejections == 1
    status = warehouse_status(wh, datetime.now(timezone.utc))
    assert status["status"] == "UNKNOWN" and status["heartbeat"]["authenticated"] is False
    assert "rechazó el reto" in status["note"]


def test_respuesta_alterada_en_el_camino_es_rechazada(central_sf):
    honest = real_warehouse()

    def man_in_the_middle(request):
        response = honest(request)
        body = response.json()
        body["ciphertext"] = body["ciphertext"][:-6] + ("AAAAAA" if not body["ciphertext"].endswith("AAAAAA") else "BBBBBB")
        return httpx.Response(200, json=body)

    result = poll(central_sf, man_in_the_middle)
    assert result.outcome == REJECTED and "authentication_failed" in result.detail
    assert row(central_sf).heartbeat_rejections == 1


def test_respuesta_vieja_grabada_es_rechazada(central_sf):
    honest = real_warehouse()
    recorded = {}

    def replayer(request):
        if "body" not in recorded:  # graba la primera respuesta y luego la repite
            recorded["body"] = honest(request).json()
        return httpx.Response(200, json=recorded["body"])

    assert poll(central_sf, replayer).outcome == OK
    result = poll(central_sf, replayer)
    assert result.outcome == REJECTED and "replay" in result.detail


def test_respuesta_que_no_esta_cifrada_es_rechazada(central_sf):
    fake = lambda request: httpx.Response(200, json={"status": "ok", "last_sequence": 99})  # noqa: E731
    assert poll(central_sf, fake).outcome == REJECTED
    assert row(central_sf).reported_sequence == 0


def test_bodega_con_otra_clave_rechaza_el_reto(central_sf):
    result = poll(central_sf, real_warehouse(), secret="clave-que-no-coincide-000000000")
    assert result.outcome == REJECTED and "las claves no coinciden" in result.detail


def test_bodega_caida_pasa_a_offline(central_sf):
    assert poll(central_sf, real_warehouse()).outcome == OK

    def down(request):
        raise httpx.ConnectError("conexión rechazada")

    for _ in range(3):
        result = poll(central_sf, down)
    assert result.outcome == UNREACHABLE
    wh = row(central_sf)
    assert wh.heartbeat_failures == 3 and wh.heartbeat_rejections == 0
    later = datetime.now(timezone.utc) + timedelta(seconds=settings.ONLINE_AFTER_SECONDS + 1)
    status = warehouse_status(wh, later)
    assert status["status"] == "OFFLINE" and "Sin respuesta" in status["note"]


def test_bodega_sin_clave_configurada_no_se_consulta(central_sf):
    calls = []
    result = poll(central_sf, lambda request: calls.append(request), secret=None)
    assert result.outcome == NO_SECRET and calls == []


def test_un_heartbeat_sin_cifrar_por_rabbitmq_se_ignora(central_sf, apply, wh1):
    old_style = wh1.make("WAREHOUSE_HEARTBEAT", {"last_sequence": 50, "api_url": "http://atacante"}, sequence=0)
    assert apply(old_style) == ["ignored"]
    with central_sf() as db:
        warehouse = db.get(WarehouseEntity, "WH-001")
        assert warehouse is None or warehouse.last_heartbeat_at is None


def test_pregunta_a_todas_a_la_vez_y_una_caida_no_frena_a_las_demas(central_sf):
    key2 = hb.derive_key("clave-de-prueba-wh002-abcdefghijk")
    ok_wh1, ok_wh2 = real_warehouse(), real_warehouse(key=key2, warehouse_id="WH-002", instance="WH-002-api-1")

    def network(request):
        if request.url.host == "wh-003":
            raise httpx.ConnectTimeout("sin respuesta")
        return (ok_wh1 if request.url.host == "wh-001" else ok_wh2)(request)

    registry = {"WH-001": {"api_url": "http://wh-001", "secret": SECRET},
                "WH-002": {"api_url": "http://wh-002", "secret": "clave-de-prueba-wh002-abcdefghijk"},
                "WH-003": {"api_url": "http://wh-003", "secret": "clave-de-prueba-wh003-abcdefghijk"}}
    results = {r.warehouse_id: r.outcome for r in poll_once(central_sf, client_for(network), registry)}
    assert results == {"WH-001": OK, "WH-002": OK, "WH-003": UNREACHABLE}


def test_el_tablero_muestra_quien_respondio(central_sf, central_client):
    poll(central_sf, real_warehouse())
    [wh] = central_client.get("/warehouses").json()
    assert wh["status"] == "ONLINE" and wh["heartbeat"]["answered_by"] == "WH-001-api-3"
    assert wh["heartbeat"]["rejected_total"] == 0


def test_registro_de_bodegas_desde_la_configuracion(monkeypatch):
    monkeypatch.setattr(settings, "WAREHOUSE_HEARTBEAT_SECRETS", "WH-001=clave-uno-0123456789,WH-002=clave-dos-0123456789")
    monkeypatch.setattr(settings, "WAREHOUSE_API_URLS", "WH-002=http://10.0.0.5:8002/")
    assert warehouse_registry() == {
        "WH-001": {"api_url": "http://lb_warehouse_WH-001:80", "secret": "clave-uno-0123456789"},
        "WH-002": {"api_url": "http://10.0.0.5:8002", "secret": "clave-dos-0123456789"},
    }


@pytest.fixture(autouse=True)
def clear_key_cache():
    central_heartbeat._key.cache_clear()
    yield
