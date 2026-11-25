"""Heartbeat cifrado contra la plataforma real: bodega WH-001 + central en Docker."""

import os
import re
from pathlib import Path

import httpx
import pytest
from platform_common import secure_heartbeat as hb

from test_central_integration import CENTRAL_URL, wait_until

ROOT = Path(__file__).resolve().parents[2]


def warehouse_secret() -> str:
    """Clave de desarrollo de WH-001 (docker/components/wh_001/.env), o HEARTBEAT_SECRET si está definida."""
    text = (ROOT / "docker" / "components" / "wh_001" / ".env").read_text(encoding="utf-8")
    return os.getenv("HEARTBEAT_SECRET") or re.search(r"^HEARTBEAT_SECRET=(.+)$", text, re.M).group(1).strip()


def send(api, key, warehouse_id="WH-001"):
    challenge = hb.new_challenge(warehouse_id)
    envelope = hb.seal(key, hb.CHALLENGE, warehouse_id, challenge)
    return challenge, envelope, api.post("/sync/heartbeat", json=envelope)


def test_la_bodega_real_responde_cifrado_a_quien_tiene_la_clave(api):
    key = hb.derive_key(warehouse_secret())
    challenge, _, response = send(api, key)
    assert response.status_code == 200, response.text
    status = hb.verify_response(key, "WH-001", challenge, response.json())
    assert status["instance"].startswith("WH-001-api-") and "last_sequence" in status


def test_la_bodega_real_rechaza_a_una_central_falsa(api):
    _, _, response = send(api, hb.derive_key("central-falsa-sin-la-clave-0000"))
    assert response.status_code == 401


def test_la_bodega_real_no_responde_dos_veces_el_mismo_reto(api):
    _, envelope, first = send(api, hb.derive_key(warehouse_secret()))
    assert first.status_code == 200
    # Cada instancia de la API recuerda sus retos y el balanceador reparte entre las 3: las repeticiones
    # que llegan a la instancia que ya respondió dan 409
    codes = [api.post("/sync/heartbeat", json=envelope).status_code for _ in range(4)]
    assert 409 in codes and set(codes) <= {200, 409}


def test_la_central_le_pregunta_y_la_ve_online_verificada():
    try:
        httpx.get(f"{CENTRAL_URL}/health", timeout=3)
    except httpx.HTTPError:
        pytest.skip(f"La central no responde en {CENTRAL_URL}")

    def verified():
        wh = next((w for w in httpx.get(f"{CENTRAL_URL}/warehouses", timeout=5).json() if w["warehouse_id"] == "WH-001"), None)
        return wh and wh["status"] == "ONLINE" and wh["heartbeat"]["authenticated"] and wh

    wh = wait_until(verified, timeout=60, what="que la central verifique a WH-001")
    assert wh["heartbeat"]["answered_by"].startswith("WH-001-api-")
