"""Solicitudes de productos nuevos que llegan de las bodegas."""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from platform_common.auth import ADMIN_GLOBAL, Principal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from central import platform_commands
from central.database import get_db
from central.models import ProductRequestEntity
from central.projection import now_utc
from central.routers.catalog import UniversalProductCreate, create_universal_product, view
from central.security import require

router = APIRouter(prefix="/product-requests", tags=["Solicitudes de productos"])
logger = logging.getLogger("central.product_requests")


class Approval(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    product: UniversalProductCreate
    note: Optional[str] = Field(None, max_length=500)


class Rejection(BaseModel):
    note: str = Field(..., min_length=3, max_length=500)


def request_view(row: ProductRequestEntity) -> dict:
    return {"id": row.id, "warehouse_id": row.warehouse_id, "request_id": row.request_id, "request_code": row.request_code,
            "status": row.status, "product": row.product, "requested_by": row.requested_by,
            "approved_by_warehouse": row.approved_by_warehouse, "decided_by": row.decided_by, "decided_at": row.decided_at,
            "decision_note": row.decision_note, "sku": row.sku, "created_at": row.created_at}


def _pending(db: Session, request_id: int) -> ProductRequestEntity:
    row = db.get(ProductRequestEntity, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if row.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"La solicitud ya está {row.status}")
    return row


@router.get("", summary="Solicitudes de productos (por defecto, las pendientes)")
def list_requests(status: Optional[Literal["PENDING", "APPROVED", "REJECTED"]] = Query("PENDING"),
                  db: Session = Depends(get_db), _: Principal = Depends(require(ADMIN_GLOBAL))):
    query = db.query(ProductRequestEntity)
    if status:
        query = query.filter(ProductRequestEntity.status == status)
    return [request_view(r) for r in query.order_by(ProductRequestEntity.id.desc()).all()]


@router.post("/{request_id}/approve", summary="Aprobar: crea el producto universal y avisa a la bodega que lo pidió")
def approve(request_id: int, data: Approval, db: Session = Depends(get_db), principal: Principal = Depends(require(ADMIN_GLOBAL))):
    row = _pending(db, request_id)
    product = create_universal_product(db, data.product, principal)
    row.status, row.sku, row.decision_note = "APPROVED", product.sku, data.note
    row.decided_by, row.decided_at = principal.username, now_utc()
    platform_commands.resolve_product_request(db, row.warehouse_id, row.request_id, "APPROVED", data.note, product.sku)
    db.commit()
    return {"request": request_view(row), "product": view(product)}


@router.post("/{request_id}/reject", summary="Rechazar y avisar a la bodega")
def reject(request_id: int, data: Rejection, db: Session = Depends(get_db), principal: Principal = Depends(require(ADMIN_GLOBAL))):
    row = _pending(db, request_id)
    row.status, row.decision_note = "REJECTED", data.note
    row.decided_by, row.decided_at = principal.username, now_utc()
    platform_commands.resolve_product_request(db, row.warehouse_id, row.request_id, "REJECTED", data.note, None)
    db.commit()
    return request_view(row)
