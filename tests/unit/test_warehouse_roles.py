"""Permisos de la API de bodega con tokens reales (la bodega verifica con la clave pública de la central)."""

from datetime import datetime, timedelta, timezone

import pytest
from platform_common import auth

from app.config import settings
from app.security import TrustedKeyEntity, key_store


@pytest.fixture
def signer():
    return auth.TokenSigner(auth.generate_private_key())


@pytest.fixture
def secured(client, monkeypatch, signer):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(key_store, "verifier", auth.TokenVerifier({signer.kid: signer.public_b64}))

    def headers(role, warehouse_id="WH-TEST", username=None):
        principal = auth.Principal(username or f"{role.lower()}.test", role, warehouse_id=warehouse_id
                                   if role in auth.WAREHOUSE_ROLES else None)
        return {"Authorization": f"Bearer {signer.issue(principal, timedelta(minutes=5))}"}

    return headers


@pytest.fixture
def product(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    created = client.post("/products", json={"sku": "ROL-1", "name": "Producto", "unit": "UNIDAD"}).json()
    client.post("/inventory/movements", json={"product_id": created["id"], "type": "RECEIPT", "quantity": 10})
    return created


def test_sin_token_responde_401(product, client, secured):
    assert client.get(f"/inventory/{product['id']}").status_code == 401


def test_el_registrador_registra_entradas_y_queda_como_autor(product, client, secured):
    response = client.post("/inventory/movements", headers=secured(auth.REGISTRADOR, username="ana"),
                           json={"product_id": product["id"], "type": "SHIPMENT", "quantity": 2})
    assert response.status_code == 201
    assert response.json()["created_by"] == "ana"


def test_el_registrador_no_aplica_ajustes_directos(product, client, secured):
    response = client.post("/inventory/movements", headers=secured(auth.REGISTRADOR),
                           json={"product_id": product["id"], "type": "ADJUSTMENT", "quantity": -1})
    assert response.status_code == 403 and "solicitud" in response.json()["detail"]


def test_un_usuario_de_otra_bodega_no_entra(product, client, secured):
    response = client.post("/inventory/movements", headers=secured(auth.REGISTRADOR, warehouse_id="WH-999"),
                           json={"product_id": product["id"], "type": "RECEIPT", "quantity": 1})
    assert response.status_code == 403


def test_el_lider_consulta_pero_no_mueve_inventario(product, client, secured):
    assert client.get(f"/inventory/{product['id']}", headers=secured(auth.LIDER)).status_code == 200
    response = client.post("/inventory/movements", headers=secured(auth.LIDER),
                           json={"product_id": product["id"], "type": "RECEIPT", "quantity": 1})
    assert response.status_code == 403


def test_solo_la_central_lee_la_sincronizacion(product, client, secured):
    assert client.get("/sync/events", headers=secured(auth.SERVICE)).status_code == 200
    assert client.get("/sync/events", headers=secured(auth.ADMIN_BODEGA)).status_code == 403


def test_la_clave_publica_se_guarda_en_la_base_de_la_bodega(client, monkeypatch, session_factory, signer):
    """Con la clave guardada, la bodega verifica tokens aunque la central no responda."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(key_store, "verifier", auth.TokenVerifier())
    with session_factory() as db:
        db.add(TrustedKeyEntity(kid=signer.kid, public_key=signer.public_b64, fetched_at=datetime.now(timezone.utc)))
        db.commit()
    token = signer.issue(auth.Principal("admin", auth.ADMIN_BODEGA, warehouse_id="WH-TEST"), timedelta(minutes=5))
    assert client.get("/products", headers={"Authorization": f"Bearer {token}"}).status_code == 200
