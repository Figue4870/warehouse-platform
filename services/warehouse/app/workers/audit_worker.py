"""Audit worker: deja un rastro de auditoría de cada evento y revisa que el saldo coincida con su historial."""

import logging

from platform_common.events import EventEnvelope

import app.all_models  # noqa: F401
from app.database import SessionLocal
from app.events import types as events
from app.events.outbox import OutboxWriter
from app.events.payloads import alert_payload
from app.modules.alerts.alerts_service import INCONSISTENCY, AlertService
from app.modules.alerts.models import InventoryAlertEntity
from app.modules.inventory.inventory_repository import InventoryRepository
from app.workers.base import parse_or_permanent, run_consumer

logger = logging.getLogger("warehouse.audit")


def check_ledger(db, product_id: int) -> InventoryAlertEntity | None:
    """El último movimiento guarda el saldo resultante; si no coincide con inventory, hubo un cambio por fuera
    de la API. Se alerta una sola vez mientras la alerta anterior siga sin revisar."""
    repo = InventoryRepository(db)
    row = repo.get_by_product_for_update(product_id)
    latest = repo.latest_movement(product_id)
    if row is None or latest is None:
        return None
    inventory, product = row
    if (latest.quantity_after, latest.reserved_after) == (inventory.quantity, inventory.reserved_quantity):
        return None
    open_alert = (db.query(InventoryAlertEntity)
                  .filter(InventoryAlertEntity.product_id == product_id, InventoryAlertEntity.type == INCONSISTENCY,
                          InventoryAlertEntity.acknowledged_at.is_(None)).first())
    if open_alert is not None:
        return None
    alert = AlertService(db).register(
        product_id, INCONSISTENCY,
        f"Saldo de {product.sku} distinto a su historial: inventario {inventory.quantity}/{inventory.reserved_quantity}, "
        f"último movimiento {latest.quantity_after}/{latest.reserved_after} (disponible/reservado)",
        latest.id,
    )
    OutboxWriter(db).add(events.INVENTORY_INCONSISTENCY, "alert", alert.id, alert_payload(alert, product))
    db.commit()
    logger.warning("Desfase detectado automáticamente", extra={"event": "inventory_inconsistency_detected",
                   "context": {"sku": product.sku, "alert_id": alert.id}})
    return alert


def make_handler(session_factory):
    def handle(body, properties, routing_key):
        event: EventEnvelope = parse_or_permanent(body)
        logger.info(
            "Evento auditado",
            extra={
                "event": "audit_event",
                "context": {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "sequence": event.sequence,
                    "aggregate": event.payload.get("sku") or event.payload.get("job_id"),
                    "occurred_at": event.occurred_at,
                },
            },
        )
        if event.event_type == events.INVENTORY_MOVEMENT_RECORDED and event.payload.get("product_id"):
            with session_factory() as db:
                check_ledger(db, int(event.payload["product_id"]))

    return handle


handle = make_handler(SessionLocal)


def main() -> None:
    run_consumer("audit", handle, "audit-worker")


if __name__ == "__main__":
    main()
