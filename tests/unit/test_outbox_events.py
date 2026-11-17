"""Fase 3: cada cambio deja su evento en el outbox, en la MISMA transacción."""

from app.events.models import OutboxEventEntity
from app.modules.alerts.alerts_service import AlertService


def events(session_factory, event_type=None):
    with session_factory() as db:
        query = db.query(OutboxEventEntity).order_by(OutboxEventEntity.id)
        if event_type:
            query = query.filter(OutboxEventEntity.event_type == event_type)
        return query.all()


def test_crear_producto_genera_product_created(client, create_product, session_factory):
    product = create_product(sku="EVT-1", retail_price="1000")
    [event] = events(session_factory, "PRODUCT_CREATED")
    assert event.routing_key == "product.created"
    assert event.aggregate_id == "EVT-1"
    assert event.payload["sku"] == "EVT-1"
    assert event.payload["warehouse_id"] == "WH-TEST"
    assert event.payload["retail_price"] == "1000.00"
    assert event.payload["product_version"] == 1
    assert event.published_at is None and event.sequence is None  # pendiente: lo publicará el relay
    assert product["version"] == 1


def test_movimiento_genera_evento_con_saldo_y_version(client, create_product, move, session_factory):
    product = create_product(sku="EVT-2")
    move(product["id"], "RECEIPT", 10, reference="FAC-9")
    move(product["id"], "SHIPMENT", 4)
    movements = events(session_factory, "INVENTORY_MOVEMENT_RECORDED")
    assert [e.payload["type"] for e in movements] == ["RECEIPT", "SHIPMENT"]
    assert movements[1].routing_key == "inventory.movement.recorded"
    assert movements[1].payload["quantity_after"] == 6
    assert movements[1].payload["sku"] == "EVT-2"
    # la versión del inventario sube con cada movimiento (sirve para sincronizar sin pisar datos nuevos)
    assert [e.payload["inventory_version"] for e in movements] == [1, 2]
    assert client.get(f"/inventory/{product['id']}").json()["version"] == 2


def test_rollback_tampoco_deja_eventos(client, create_product, move, session_factory, monkeypatch):
    product = create_product()
    move(product["id"], "RECEIPT", 1)

    def explode(*args, **kwargs):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(AlertService, "register", explode)
    assert move(product["id"], "SHIPMENT", 1).status_code == 500
    # Solo el evento de la entrada: el de la salida fallida se revirtió junto con todo lo demás
    assert len(events(session_factory, "INVENTORY_MOVEMENT_RECORDED")) == 1


def test_correlation_id_viaja_del_header_al_evento(client, create_product, session_factory):
    response = client.post("/products", json={"sku": "COR-1", "name": "x", "unit": "U"},
                           headers={"X-Correlation-ID": "COR-PRUEBA-001"})
    assert response.headers["X-Correlation-ID"] == "COR-PRUEBA-001"
    assert events(session_factory, "PRODUCT_CREATED")[0].correlation_id == "COR-PRUEBA-001"
