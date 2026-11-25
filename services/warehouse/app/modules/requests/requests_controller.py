from typing import Annotated, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, Query, status
from platform_common.auth import ADMIN_GLOBAL, Principal
from pydantic import Field
from sqlalchemy.orm import Session

from app.cache import InventoryCache, get_inventory_cache
from app.database import get_db
from app.modules.requests.requests_service import RequestService
from app.modules.requests.schemas import RequestCreate, RequestDecision, RequestResponse
from app.security import OPERATORS, WAREHOUSE_ADMINS, require

router = APIRouter(prefix="/requests", tags=["Solicitudes"])


@router.post("", response_model=RequestResponse, status_code=status.HTTP_201_CREATED,
             summary="Crear una solicitud: producto nuevo o ajuste por desfase")
def create_request(payload: Annotated[RequestCreate, Field(discriminator="type")] = Body(...),
                   db: Session = Depends(get_db), cache: InventoryCache = Depends(get_inventory_cache),
                   principal: Principal = Depends(require(*OPERATORS))):
    return RequestService(db, cache).create(payload, principal)


@router.get("", response_model=List[RequestResponse], summary="Solicitudes (el registrador solo ve las suyas)")
def list_requests(status_filter: Optional[Literal["PENDING", "FORWARDED", "APPROVED", "REJECTED"]] = Query(None, alias="status"),
                  limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                  principal: Principal = Depends(require(*OPERATORS, ADMIN_GLOBAL))):
    return RequestService(db).list(principal, status_filter, limit)


@router.post("/{request_id}/approve", response_model=RequestResponse, summary="Aprobar (el ajuste se aplica al inventario)")
def approve_request(request_id: int, decision: RequestDecision = Body(default_factory=RequestDecision),
                    db: Session = Depends(get_db), cache: InventoryCache = Depends(get_inventory_cache),
                    principal: Principal = Depends(require(*WAREHOUSE_ADMINS))):
    return RequestService(db, cache).approve(request_id, principal, decision.note)


@router.post("/{request_id}/reject", response_model=RequestResponse, summary="Rechazar")
def reject_request(request_id: int, decision: RequestDecision = Body(default_factory=RequestDecision),
                   db: Session = Depends(get_db), principal: Principal = Depends(require(*WAREHOUSE_ADMINS))):
    return RequestService(db).reject(request_id, principal, decision.note)
