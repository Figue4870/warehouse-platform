"""Respuesta de la bodega al heartbeat cifrado de la central (POST /sync/heartbeat)."""

import logging
from datetime import datetime, timezone
from functools import lru_cache

from platform_common import secure_heartbeat as hb
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.events.models import OutboxEventEntity

logger = logging.getLogger("warehouse.heartbeat")

# Retos ya respondidos por esta instancia de la API
replay_guard = hb.ReplayGuard()


class HeartbeatNotConfigured(RuntimeError):
    """La bodega no tiene HEARTBEAT_SECRET y no puede responder."""


@lru_cache(maxsize=4)
def _key(secret: str) -> bytes:
    return hb.derive_key(secret)


def heartbeat_key() -> bytes:
    if not settings.HEARTBEAT_SECRET:
        raise HeartbeatNotConfigured("Esta bodega no tiene HEARTBEAT_SECRET: no puede responder heartbeats")
    return _key(settings.HEARTBEAT_SECRET)


def heartbeat_status(db: Session) -> dict:
    """Estado que viaja cifrado en la respuesta: última secuencia publicada, pendientes del outbox e instancia."""
    last_sequence = (
        db.query(func.max(OutboxEventEntity.sequence)).filter(OutboxEventEntity.published_at.isnot(None)).scalar() or 0
    )
    pending = db.query(func.count(OutboxEventEntity.id)).filter(OutboxEventEntity.published_at.is_(None)).scalar()
    return {
        "last_sequence": last_sequence,
        "outbox_pending": pending,
        "instance": settings.INSTANCE_NAME,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def answer(db: Session, envelope: dict) -> dict:
    """Valida el reto de la central y devuelve la respuesta cifrada. Los rechazos quedan en el log."""
    key = heartbeat_key()
    try:
        return hb.answer_challenge(key, settings.WAREHOUSE_ID, envelope, heartbeat_status(db), replay_guard)
    except hb.HeartbeatError as error:
        logger.warning("Reto de heartbeat rechazado", extra={"event": "heartbeat_challenge_rejected",
                                                             "context": {"reason": error.reason, "detail": str(error)}})
        raise
