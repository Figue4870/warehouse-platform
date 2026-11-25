"""Solicitudes (ajustes y productos nuevos), catálogo universal, lotes e historial de compras y ventas."""

from decimal import Decimal

from platform_common import commands as c
from platform_common.commands import CommandEnvelope

from app.config import settings
from app.events.models import OutboxEventEntity
from app.modules.alerts.models import InventoryAlertEntity
from app.modules.catalog.catalog_service import APPLIED, STALE, CatalogService
from app.modules.inventory.models import InventoryEntity
from app.workers.audit_worker import check_ledger
from app.workers.command_worker import handle_platform_command


def _events(session_factory, event_type):
    with session_factory() as db:
        return [e.payload for e in db.query(OutboxEventEntity).filter(OutboxEventEntity.event_type == event_type)]


def test_reportar_un_desfase_crea_la_alerta_y_avisa_al_administrador(client, create_product, move, session_factory):
    product = create_product()
    move(product["id"], "RECEIPT", 20)
    response = client.post("/requests", json={"type": "ADJUSTMENT", "product_id": product["id"],
                                              "counted_quantity": 17, "reason": "Conteo físico de cierre"})
    assert response.status_code == 201
    request = response.json()
    assert request["status"] == "PENDING" and request["details"]["difference"] == -3
    with session_factory() as db:
        assert db.get(InventoryAlertEntity, request["alert_id"]).type == "DISCREPANCY"
    assert len(_events(session_factory, "STOCK_DISCREPANCY")) == 1


def test_aprobar_el_ajuste_aplica_la_diferencia_sin_duplicar_la_alerta(client, create_product, move, session_factory):
    product = create_product()
    move(product["id"], "RECEIPT", 20)
    request = client.post("/requests", json={"type": "ADJUSTMENT", "product_id": product["id"],
                                             "counted_quantity": 17, "reason": "Conteo"}).json()
    move(product["id"], "SHIPMENT", 5)
    approved = client.post(f"/requests/{request['id']}/approve", json={"note": "Verificado"}).json()
    assert approved["status"] == "APPROVED" and approved["movement_id"]
    assert client.get(f"/inventory/{product['id']}").json()["quantity"] == 12
    assert len(_events(session_factory, "STOCK_DISCREPANCY")) == 1
    assert client.post(f"/requests/{request['id']}/approve").status_code == 409


def test_conteo_igual_al_sistema_no_es_desfase(client, create_product, move):
    product = create_product()
    move(product["id"], "RECEIPT", 4)
    response = client.post("/requests", json={"type": "ADJUSTMENT", "product_id": product["id"],
                                              "counted_quantity": 4, "reason": "Conteo"})
    assert response.status_code == 409


def test_producto_nuevo_pasa_por_la_bodega_y_luego_por_la_central(client, session_factory):
    request = client.post("/requests", json={"type": "NEW_PRODUCT", "name": "Arnés de seguridad", "unit": "UNIDAD",
                                             "category": "EPP", "reason": "Lo piden los clientes"}).json()
    forwarded = client.post(f"/requests/{request['id']}/approve").json()
    assert forwarded["status"] == "FORWARDED"
    submitted = _events(session_factory, "PRODUCT_REQUEST_SUBMITTED")
    assert submitted[0]["request_id"] == request["id"] and submitted[0]["product"]["name"] == "Arnés de seguridad"

    command = CommandEnvelope(command_id="c1", command_type=c.PRODUCT_REQUEST_RESOLVED, warehouse_id="WH-TEST",
                              issued_at="2026-11-24T00:00:00Z",
                              payload={"request_id": request["id"], "status": "APPROVED", "sku": "EPP-ARNES-007"})
    with session_factory() as db:
        assert handle_platform_command(db, command) == "APPROVED"
        assert handle_platform_command(db, command) == "NOOP"
    final = client.get("/requests").json()[0]
    assert final["status"] == "APPROVED" and final["details"]["sku"] == "EPP-ARNES-007"


def test_el_catalogo_universal_ignora_versiones_viejas(session_factory):
    data = {"sku": "U-1", "name": "Original", "unit": "UNIDAD", "catalog_version": 2}
    with session_factory() as db:
        service = CatalogService(db)
        assert service.upsert(data) == APPLIED
        assert service.upsert({**data, "name": "Viejo", "catalog_version": 1}) == STALE
        assert service.upsert({**data, "catalog_version": 2}) == STALE
        db.commit()
        assert service.get("U-1").name == "Original"


def test_catalogo_estricto_y_agregar_desde_el_universal(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "CATALOG_STRICT", True)
    assert client.post("/products", json={"sku": "FUERA-1", "name": "X", "unit": "UNIDAD"}).status_code == 422
    with session_factory() as db:
        CatalogService(db).upsert({"sku": "U-2", "name": "Casco", "unit": "UNIDAD", "retail_price": "32000",
                                   "aliases": ["Hard hat"], "catalog_version": 1})
        db.commit()
    listed = client.get("/catalog/universal", params={"search": "hard"}).json()
    assert listed[0]["sku"] == "U-2" and listed[0]["local_product_id"] is None
    created = client.post("/catalog/local", json={"sku": "U-2", "lot": "L-1", "retail_price": "35000.00"})
    assert created.status_code == 201
    assert created.json()["name"] == "Casco" and created.json()["retail_price"] == "35000.00"
    assert client.get("/catalog/universal").json()[0]["local_product_id"] == created.json()["id"]


def test_los_lotes_se_verifican_en_las_salidas(client, create_product, move):
    product = create_product()
    assert move(product["id"], "RECEIPT", 10, lot="L-A").status_code == 201
    assert move(product["id"], "RECEIPT", 5, lot="L-B").status_code == 201
    unknown = move(product["id"], "SHIPMENT", 1, lot="L-Z")
    assert unknown.status_code == 409 and "no existe" in unknown.json()["detail"]
    too_much = move(product["id"], "SHIPMENT", 6, lot="L-B")
    assert too_much.status_code == 409 and "no alcanza" in too_much.json()["detail"]
    assert move(product["id"], "SHIPMENT", 4, lot="L-A").status_code == 201
    lots = {row["lot"]: row["quantity"] for row in client.get(f"/inventory/{product['id']}/lots").json()}
    assert lots == {"L-A": 6, "L-B": 5}


def test_historial_de_compras_y_ventas_con_totales(client, create_product, move):
    product = create_product(supplier_cost="1000.00", retail_price="2500.00")
    move(product["id"], "RECEIPT", 10, counterparty="Proveedor S.A.")
    move(product["id"], "SHIPMENT", 3, unit_price="2400.00", counterparty="Cliente 1")
    history = client.get("/inventory/transactions").json()
    assert history["purchases_units"] == 10 and Decimal(history["purchases_total"]) == Decimal("10000")
    assert history["sales_units"] == 3 and Decimal(history["sales_total"]) == Decimal("7200")
    assert {row["counterparty"] for row in history["rows"]} == {"Proveedor S.A.", "Cliente 1"}


def test_desfase_detectado_automaticamente(client, create_product, move, session_factory):
    product = create_product()
    move(product["id"], "RECEIPT", 8)
    with session_factory() as db:
        assert check_ledger(db, product["id"]) is None
        inventory = db.query(InventoryEntity).filter(InventoryEntity.product_id == product["id"]).one()
        inventory.quantity = 50
        db.commit()
        alert = check_ledger(db, product["id"])
        assert alert is not None and alert.type == "INCONSISTENCY"
        assert check_ledger(db, product["id"]) is None
    assert len(_events(session_factory, "INVENTORY_INCONSISTENCY")) == 1
