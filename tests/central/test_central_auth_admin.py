"""Central: inicio de sesión, permisos por rol, registro de bodegas, catálogo universal y solicitudes."""

import pytest
from platform_common import auth
from platform_common.commands import CATALOG_UPSERT, PRODUCT_REQUEST_RESOLVED

from central import security
from central.config import settings
from central.identity import LocalDirectory
from central.models import TransferCommandEntity, UniversalProductEntity, WarehouseEntity
from central.projection import get_or_create_warehouse
from central.security import service_token

PASSWORD = "clave-de-prueba-123"


@pytest.fixture
def secured(central_client, central_sf, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    security.reset_cache()
    with central_sf() as db:
        directory = LocalDirectory()
        for username, role, warehouse_id in (("admin.global", auth.ADMIN_GLOBAL, None), ("lider", auth.LIDER, None),
                                             ("admin.wh001", auth.ADMIN_BODEGA, "WH-001"),
                                             ("admin.wh003", auth.ADMIN_BODEGA, "WH-003")):
            directory.create_user(db, username, username, role, warehouse_id, PASSWORD, "test")
        for warehouse_id in ("WH-001", "WH-002"):
            get_or_create_warehouse(db, warehouse_id)
        db.commit()

    def login(username):
        response = central_client.post("/auth/login", json={"username": username, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    yield login
    security.reset_cache()


def _commands(central_sf, command_type):
    with central_sf() as db:
        return [(c.warehouse_id, c.payload) for c in db.query(TransferCommandEntity)
                .filter(TransferCommandEntity.command_type == command_type).order_by(TransferCommandEntity.id)]


def test_login_y_token_verificable_con_la_clave_publica(central_client, secured):
    headers = secured("admin.wh001")
    me = central_client.get("/auth/me", headers=headers).json()
    assert me["role"] == auth.ADMIN_BODEGA and me["warehouse_id"] == "WH-001"
    keys = central_client.get("/auth/keys").json()["keys"]
    verifier = auth.TokenVerifier({k["kid"]: k["public_key"] for k in keys})
    assert verifier.verify(headers["Authorization"][7:]).username == "admin.wh001"


def test_contrasena_incorrecta(central_client, secured):
    response = central_client.post("/auth/login", json={"username": "lider", "password": "otra"})
    assert response.status_code == 401


def test_permisos_por_rol(central_client, secured):
    assert central_client.get("/global/summary").status_code == 401
    assert central_client.get("/reports/top-products", headers=secured("lider")).status_code == 200
    assert central_client.get("/reports/top-products", headers=secured("admin.wh001")).status_code == 403
    assert central_client.get("/admin/users", headers=secured("lider")).status_code == 403


def test_el_admin_global_asigna_credenciales_a_un_lider(central_client, secured):
    created = central_client.post("/admin/users", headers=secured("admin.global"),
                                  json={"username": "lider.ventas", "display_name": "Líder de ventas", "role": "LIDER"}).json()
    login = central_client.post("/auth/login", json={"username": "lider.ventas", "password": created["temporary_password"]})
    assert login.status_code == 200 and login.json()["user"]["role"] == "LIDER"
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert central_client.get("/auth/me", headers=headers).json()["must_change_password"] is True
    changed = central_client.post("/auth/password", headers=headers,
                                  json={"current_password": created["temporary_password"], "new_password": "nueva-clave-segura"})
    assert changed.status_code == 200


def test_un_rol_de_bodega_necesita_su_bodega(central_client, secured):
    response = central_client.post("/admin/users", headers=secured("admin.global"),
                                   json={"username": "sin.bodega", "display_name": "Sin bodega", "role": "REGISTRADOR"})
    assert response.status_code == 422


def test_registrar_bodega_aprovisiona_cola_catalogo_y_usuarios(central_client, central_sf, secured):
    headers = secured("admin.global")
    central_client.post("/catalog", headers=headers, json={"sku": "U-1", "name": "Casco", "unit": "UNIDAD"})
    response = central_client.post("/admin/warehouses", headers=headers, json={
        "nit": "900555444-1", "name": "Bodega Palmira", "location": "Palmira", "address": "Calle 30 # 20-10",
        "capacity_units": 5000})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["warehouse_id"] == "WH-003" and data["command_queue"] == "commands.WH-003"
    assert data["catalog_products_queued"] == 1
    assert {c["role"] for c in data["initial_credentials"]} == {"REGISTRADOR"}  # admin.wh003 ya existía
    assert "HEARTBEAT_SECRET=wh-" in data["env_file"] and "BE_PORT=8003" in data["env_file"]
    with central_sf() as db:
        warehouse = db.get(WarehouseEntity, "WH-003")
        assert warehouse.nit == "900555444-1" and warehouse.heartbeat_secret
    assert ("WH-003", {"sku": "U-1"}) in [(wh, {"sku": p["sku"]}) for wh, p in _commands(central_sf, CATALOG_UPSERT)]
    duplicate = central_client.post("/admin/warehouses", headers=headers, json={
        "nit": "900555444-1", "name": "Otra", "location": "Cali", "address": "Calle 1 # 1-1", "capacity_units": 10})
    assert duplicate.status_code == 409


def test_producto_universal_llega_a_todas_las_bodegas(central_client, central_sf, secured):
    response = central_client.post("/catalog", headers=secured("admin.global"), json={
        "sku": "EPP-ARNES-007", "name": "Arnés", "aliases": ["Arnés de cuerpo completo"], "unit": "UNIDAD",
        "retail_price": "120000.00"})
    assert response.status_code == 201
    targets = {wh for wh, payload in _commands(central_sf, CATALOG_UPSERT) if payload["sku"] == "EPP-ARNES-007"}
    assert targets == {"WH-001", "WH-002"}
    central_client.put("/catalog/EPP-ARNES-007", headers=secured("admin.global"), json={"name": "Arnés", "unit": "UNIDAD"})
    versions = [p["catalog_version"] for _, p in _commands(central_sf, CATALOG_UPSERT)]
    assert versions == [1, 1, 2, 2]
    assert central_client.post("/catalog", headers=secured("lider"), json={"sku": "X", "name": "X"}).status_code == 403


def test_solicitud_de_producto_nuevo_de_punta_a_punta(central_client, central_sf, apply, wh1, secured):
    event = wh1.make("PRODUCT_REQUEST_SUBMITTED", {"request_id": 7, "request_code": "REQ-WH-001-00007",
                                                   "requested_by": "registrador.wh001", "approved_by": "admin.wh001",
                                                   "product": {"name": "Arnés", "unit": "UNIDAD", "reason": "Clientes"}})
    assert apply(event, event) == ["applied", "duplicate"]
    headers = secured("admin.global")
    pending = central_client.get("/product-requests", headers=headers).json()
    assert len(pending) == 1 and pending[0]["request_code"] == "REQ-WH-001-00007"
    notifications = central_client.get("/notifications", headers=headers).json()
    assert notifications["unread"] >= 1 and any(n["kind"] == "PRODUCT_REQUEST" for n in notifications["items"])
    approved = central_client.post(f"/product-requests/{pending[0]['id']}/approve", headers=headers,
                                   json={"product": {"sku": "EPP-ARNES-007", "name": "Arnés", "unit": "UNIDAD"}})
    assert approved.status_code == 200
    resolved = _commands(central_sf, PRODUCT_REQUEST_RESOLVED)
    assert resolved == [("WH-001", {"request_id": 7, "status": "APPROVED", "note": None, "sku": "EPP-ARNES-007"})]
    with central_sf() as db:
        assert db.get(UniversalProductEntity, "EPP-ARNES-007") is not None


def test_desfase_en_una_bodega_notifica_al_admin_global(central_client, apply, wh1, secured):
    apply(wh1.make("INVENTORY_INCONSISTENCY", {"alert_id": 1, "alert_type": "INCONSISTENCY", "sku": "SKU-1",
                                               "product_name": "Casco", "message": "Saldo distinto a su historial"}))
    items = central_client.get("/notifications", headers=secured("admin.global")).json()["items"]
    assert items[0]["severity"] == "CRITICAL" and items[0]["warehouse_id"] == "WH-001"
    assert central_client.get("/notifications", headers=secured("lider")).json()["items"] == []


def test_admin_de_bodega_solo_transfiere_desde_o_hacia_su_bodega(central_client, secured):
    body = {"sku": "SKU-1", "source_warehouse_id": "WH-002", "destination_warehouse_id": "WH-001", "quantity": 1}
    assert central_client.post("/transfers", headers=secured("admin.wh003"), json=body).status_code == 403
    response = central_client.post("/transfers", headers=secured("admin.wh001"), json=body)
    assert response.status_code == 202 and response.json()["requested_by"] == "admin.wh001"


def test_token_de_servicio_para_llamar_a_las_bodegas(central_sf, secured):
    with central_sf() as db:
        token = service_token(db)
        keys = security.public_keys(db)
    principal = auth.TokenVerifier({k["kid"]: k["public_key"] for k in keys}).verify(token)
    assert principal.role == auth.SERVICE
