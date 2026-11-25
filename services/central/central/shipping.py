"""Envíos con Servientrega dentro de la Saga de transferencias (integración asíncrona).

PENDING  -> el worker pide el envío (con reintentos y espera creciente si Servientrega no responde)
CREATED / IN_TRANSIT -> el estado llega por webhook; si el webhook se pierde, el worker lo consulta
DELIVERED -> la Saga pide al destino recibir las unidades
RETURNED o ERROR -> la Saga compensa liberando la reserva del origen
"""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from central import external, saga
from central.config import settings
from central.models import ShipmentEntity, TransferEntity, WarehouseEntity
from central.projection import now_utc

logger = logging.getLogger("central.shipping")

PENDING, CREATED, IN_TRANSIT, DELIVERED, RETURNED, ERROR = "PENDING", "CREATED", "IN_TRANSIT", "DELIVERED", "RETURNED", "ERROR"
ORDER = {PENDING: 0, CREATED: 1, IN_TRANSIT: 2, DELIVERED: 3, RETURNED: 3, ERROR: 3}
APPLIED, DUPLICATE, UNKNOWN, IGNORED = "applied", "duplicate", "unknown", "ignored"


def _address(db: Session, warehouse_id: str) -> dict:
    warehouse = db.get(WarehouseEntity, warehouse_id)
    return {"warehouse_id": warehouse_id, "name": getattr(warehouse, "name", None),
            "city": getattr(warehouse, "location", None), "address": getattr(warehouse, "address", None)}


def backoff_seconds(attempts: int) -> int:
    return min(5 * 2 ** max(attempts - 1, 0), 300)


def _fail(db: Session, shipment: ShipmentEntity, reason: str) -> None:
    shipment.status, shipment.last_error, shipment.updated_at = ERROR, reason, now_utc()
    saga.shipment_failed(db, shipment.transfer_id, f"Servientrega: {reason}")


def request_pending(session_factory: sessionmaker, now=None) -> int:
    """Pide a Servientrega los envíos pendientes. Cada uno en su propia transacción."""
    now = now or now_utc()
    handled = 0
    while True:
        with session_factory() as db:
            query = (db.query(ShipmentEntity).filter(ShipmentEntity.status == PENDING, ShipmentEntity.next_attempt_at <= now)
                     .order_by(ShipmentEntity.id))
            if db.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            shipment = query.first()
            if shipment is None:
                return handled
            transfer = db.query(TransferEntity).filter(TransferEntity.transfer_id == shipment.transfer_id).first()
            shipment.attempts += 1
            try:
                created = external.create_shipment(
                    shipment.transfer_id, _address(db, transfer.source_warehouse_id),
                    _address(db, transfer.destination_warehouse_id), [{"sku": transfer.sku, "quantity": transfer.quantity}],
                    f"{settings.CENTRAL_CALLBACK_URL.rstrip('/')}/integrations/servientrega")
                shipment.status, shipment.tracking_number = created.get("status", CREATED), created["tracking_number"]
                shipment.last_error, shipment.updated_at = None, now_utc()
                logger.info("Envío creado en Servientrega", extra={"event": "shipment_created", "context": {
                    "transfer_id": shipment.transfer_id, "tracking_number": shipment.tracking_number}})
            except external.ExternalRejected as error:
                _fail(db, shipment, f"rechazó el envío ({error})")
            except external.ExternalUnavailable as error:
                if shipment.attempts >= settings.SHIPPING_MAX_ATTEMPTS:
                    _fail(db, shipment, f"no respondió después de {shipment.attempts} intentos")
                else:
                    shipment.last_error = str(error)
                    shipment.next_attempt_at = now_utc() + timedelta(seconds=backoff_seconds(shipment.attempts))
                    shipment.updated_at = now_utc()
                    logger.warning("Servientrega no respondió; se reintentará", extra={"event": "shipment_retry", "context": {
                        "transfer_id": shipment.transfer_id, "attempt": shipment.attempts, "error": str(error)}})
            db.commit()
            handled += 1


def apply_status(db: Session, reference: str, tracking_number: str, status: str, detail: str | None, source: str) -> str:
    """Webhook o consulta: solo avanza (un aviso viejo o repetido no retrocede el estado). NO hace commit."""
    shipment = (db.query(ShipmentEntity).filter(ShipmentEntity.transfer_id == reference).with_for_update().first())
    if shipment is None:
        return UNKNOWN
    if shipment.tracking_number and shipment.tracking_number != tracking_number:
        return IGNORED
    if ORDER.get(status, -1) <= ORDER[shipment.status] or shipment.status in (DELIVERED, RETURNED, ERROR):
        return DUPLICATE
    shipment.status, shipment.tracking_number, shipment.updated_at = status, tracking_number, now_utc()
    if status == DELIVERED:
        saga.shipment_delivered(db, reference, tracking_number)
    elif status == RETURNED:
        saga.shipment_failed(db, reference, f"Servientrega devolvió el envío al origen ({detail or 'sin detalle'})")
    logger.info("Estado del envío actualizado", extra={"event": "shipment_status", "context": {
        "transfer_id": reference, "tracking_number": tracking_number, "status": status, "source": source}})
    return APPLIED


def poll_active(session_factory: sessionmaker, now=None) -> int:
    """Por si un webhook no llegó: consulta los envíos que llevan rato sin novedades."""
    now = now or now_utc()
    stale_before = now - timedelta(seconds=settings.SHIPPING_POLL_SECONDS * 3)
    changed = 0
    with session_factory() as db:
        active = (db.query(ShipmentEntity).filter(ShipmentEntity.status.in_((CREATED, IN_TRANSIT)),
                                                  ShipmentEntity.updated_at < stale_before).limit(50).all())
        targets = [(s.transfer_id, s.tracking_number) for s in active]
    for transfer_id, tracking_number in targets:
        try:
            remote = external.get_shipment(tracking_number)
        except external.ExternalUnavailable:
            continue
        with session_factory() as db:
            if apply_status(db, transfer_id, tracking_number, remote["status"], remote.get("detail"), "poll") == APPLIED:
                changed += 1
            else:
                shipment = db.query(ShipmentEntity).filter(ShipmentEntity.transfer_id == transfer_id).first()
                shipment.updated_at = now_utc()  # sin cambios: la próxima consulta espera otro intervalo
            db.commit()
    return changed

