"""Comandos de la plataforma hacia las bodegas (catálogo universal y respuestas a solicitudes).

Se guardan en el mismo outbox de comandos de la Saga (transfer_commands) y los publica el command relay en la
cola commands.<bodega>. Así un comando nunca se pierde aunque RabbitMQ o la bodega estén caídos.
"""

import uuid
from decimal import Decimal
from typing import Iterable

from platform_common.commands import CATALOG_UPSERT, PRODUCT_REQUEST_RESOLVED
from platform_common.correlation import get_correlation_id
from sqlalchemy.orm import Session

from central.config import heartbeat_secrets
from central.models import TransferCommandEntity, UniversalProductEntity, WarehouseEntity


def _money(value):
    return None if value is None else str(Decimal(str(value)).quantize(Decimal("0.01")))


def catalog_payload(product: UniversalProductEntity) -> dict:
    return {
        "sku": product.sku, "name": product.name, "aliases": list(product.aliases or []),
        "description": product.description, "category": product.category, "unit": product.unit,
        "supplier": product.supplier, "supplier_cost": _money(product.supplier_cost),
        "retail_price": _money(product.retail_price), "wholesale_price": _money(product.wholesale_price),
        "is_active": product.is_active, "catalog_version": product.catalog_version,
    }


def known_warehouses(db: Session) -> list[str]:
    ids = {row[0] for row in db.query(WarehouseEntity.warehouse_id).all()}
    return sorted(ids | set(heartbeat_secrets()))


def enqueue(db: Session, warehouse_id: str, command_type: str, payload: dict) -> TransferCommandEntity:
    command = TransferCommandEntity(command_id=str(uuid.uuid4()), transfer_id=None, warehouse_id=warehouse_id,
                                    command_type=command_type, payload=payload, correlation_id=get_correlation_id())
    db.add(command)
    return command


def publish_catalog_product(db: Session, product: UniversalProductEntity, warehouses: Iterable[str] | None = None) -> int:
    """Un comando por bodega. No hace commit: se guarda junto con el cambio del catálogo."""
    targets = list(warehouses) if warehouses is not None else known_warehouses(db)
    payload = catalog_payload(product)
    for warehouse_id in targets:
        enqueue(db, warehouse_id, CATALOG_UPSERT, payload)
    return len(targets)


def send_full_catalog(db: Session, warehouse_id: str) -> int:
    products = db.query(UniversalProductEntity).order_by(UniversalProductEntity.sku).all()
    for product in products:
        enqueue(db, warehouse_id, CATALOG_UPSERT, catalog_payload(product))
    return len(products)


def resolve_product_request(db: Session, warehouse_id: str, request_id: int, status: str, note: str | None,
                            sku: str | None) -> None:
    enqueue(db, warehouse_id, PRODUCT_REQUEST_RESOLVED,
            {"request_id": request_id, "status": status, "note": note, "sku": sku})
