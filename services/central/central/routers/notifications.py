"""Notificaciones del sistema (bodegas caídas, desfases, solicitudes, contratos...)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from platform_common.auth import ADMIN_BODEGA, REGISTRADOR, Principal
from sqlalchemy import and_
from sqlalchemy.orm import Session

from central.database import get_db
from central.models import NotificationEntity
from central.projection import now_utc
from central.security import ANY_USER, require

router = APIRouter(prefix="/notifications", tags=["Notificaciones"])


def _visible(principal: Principal):
    condition = NotificationEntity.audience_role == principal.role
    if principal.role in (ADMIN_BODEGA, REGISTRADOR):
        condition = and_(condition, NotificationEntity.warehouse_id == principal.warehouse_id)
    return condition


@router.get("", summary="Notificaciones para mi rol")
def list_notifications(unread: bool = False, limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
                       principal: Principal = Depends(require(*ANY_USER))):
    query = db.query(NotificationEntity).filter(_visible(principal))
    if unread:
        query = query.filter(NotificationEntity.read_at.is_(None))
    rows = query.order_by(NotificationEntity.id.desc()).limit(limit).all()
    unread_count = db.query(NotificationEntity).filter(_visible(principal), NotificationEntity.read_at.is_(None)).count()
    return {"unread": unread_count, "items": [
        {"id": n.id, "kind": n.kind, "severity": n.severity, "title": n.title, "body": n.body,
         "warehouse_id": n.warehouse_id, "created_at": n.created_at, "read_at": n.read_at} for n in rows]}


@router.post("/{notification_id}/read", summary="Marcar como leída")
def mark_read(notification_id: int, db: Session = Depends(get_db), principal: Principal = Depends(require(*ANY_USER))):
    row = db.query(NotificationEntity).filter(NotificationEntity.id == notification_id, _visible(principal)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    if row.read_at is None:
        row.read_at, row.read_by = now_utc(), principal.username
        db.commit()
    return {"id": row.id, "read_at": row.read_at}


@router.post("/read-all", summary="Marcar todas como leídas")
def mark_all(db: Session = Depends(get_db), principal: Principal = Depends(require(*ANY_USER))):
    changed = (db.query(NotificationEntity).filter(_visible(principal), NotificationEntity.read_at.is_(None))
               .update({"read_at": now_utc(), "read_by": principal.username}, synchronize_session=False))
    db.commit()
    return {"marked": changed}
