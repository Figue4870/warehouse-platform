"""Fase 7: la proyección es idempotente, no pisa datos nuevos y detecta huecos."""

from central.models import (CatalogProductEntity, MovementHistoryEntity, ProcessedEventEntity,
                            WarehouseEntity, WarehouseInventoryEntity)


def test_un_movimiento_actualiza_saldo_historial_y_catalogo(apply, get_row, wh1):
    assert apply(wh1.product(), wh1.movement(quantity=10, after=10)) == ["applied", "applied"]
    inventory = get_row(WarehouseInventoryEntity, {"warehouse_id": "WH-001", "sku": "SKU-1"})
    assert inventory.quantity == 10 and inventory.version == 1
    assert get_row(CatalogProductEntity, "SKU-1").retail_price == 100
    warehouse = get_row(WarehouseEntity, "WH-001")
    assert warehouse.watermark_sequence == 2 and warehouse.max_received_sequence == 2


def test_evento_repetido_no_se_cuenta_dos_veces(apply, central_sf, wh1):
    movement = wh1.movement()
    assert apply(movement, movement) == ["applied", "duplicate"]
    with central_sf() as db:
        assert db.query(MovementHistoryEntity).count() == 1
        assert db.query(ProcessedEventEntity).count() == 1


def test_eventos_desordenados_no_pisan_el_saldo_nuevo(apply, get_row, wh1):
    first = wh1.movement(quantity=10, after=10, version=1)
    second = wh1.movement(m_type="SHIPMENT", quantity=4, after=6, version=2)
    apply(second, first)  # llegan al revés
    assert get_row(WarehouseInventoryEntity, {"warehouse_id": "WH-001", "sku": "SKU-1"}).quantity == 6


def test_el_watermark_se_detiene_en_el_hueco(apply, get_row, wh1):
    e1, e2, e3, e4 = (wh1.movement(after=i, version=i) for i in range(1, 5))
    apply(e1, e2, e4)  # falta la secuencia 3
    warehouse = get_row(WarehouseEntity, "WH-001")
    assert warehouse.watermark_sequence == 2 and warehouse.max_received_sequence == 4
    apply(e3)  # llega la que faltaba
    assert get_row(WarehouseEntity, "WH-001").watermark_sequence == 4
