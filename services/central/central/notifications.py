"""Notificaciones del sistema para los usuarios de la central."""

from typing import Optional

from sqlalchemy.orm import Session

from central.models import NotificationEntity

INFO, WARNING, CRITICAL = "INFO", "WARNING", "CRITICAL"


def notify(db: Session, audience_role: str, kind: str, title: str, body: str = "", severity: str = INFO,
           warehouse_id: Optional[str] = None, dedupe_key: Optional[str] = None) -> Optional[NotificationEntity]:
    """Agrega una notificación. Con dedupe_key, la misma situación no se notifica dos veces. No hace commit."""
    if dedupe_key and db.query(NotificationEntity.id).filter(NotificationEntity.dedupe_key == dedupe_key).first():
        return None
    row = NotificationEntity(audience_role=audience_role, kind=kind, title=title[:200], body=body, severity=severity,
                             warehouse_id=warehouse_id, dedupe_key=dedupe_key)
    db.add(row)
    return row
