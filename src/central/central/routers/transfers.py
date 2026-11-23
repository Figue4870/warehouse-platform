"""API de transferencias (Saga orquestada por la central)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from central.database import get_db
from central.models import TransferEntity
from central.saga import STATES, SagaError, TransferRequest, create_transfer, transfer_detail, transfer_summary

router = APIRouter(prefix="/transfers", tags=["Transfers (Saga, Fase 6)"])


@router.post("", status_code=status.HTTP_202_ACCEPTED,
             summary="Pedir una transferencia entre bodegas (asíncrona: responde 202 y la Saga trabaja por detrás)")
def request_transfer(request: TransferRequest, response: Response, db: Session = Depends(get_db)):
    try:
        transfer = create_transfer(db, request)
    except SagaError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message)
    response.headers["Location"] = f"/transfers/{transfer.transfer_id}"
    return transfer_summary(transfer)


@router.get("", summary="Transferencias (las más recientes primero)")
def list_transfers(state: Optional[str] = Query(None, description=f"Uno de: {', '.join(STATES)}"),
                   sku: Optional[str] = None, limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    query = db.query(TransferEntity)
    if state:
        query = query.filter(TransferEntity.state == state.upper())
    if sku:
        query = query.filter(TransferEntity.sku == sku)
    return [transfer_summary(t) for t in query.order_by(TransferEntity.id.desc()).limit(limit).all()]


@router.get("/{transfer_id}", summary="Estado de una transferencia con su historial paso a paso")
def get_transfer(transfer_id: str, db: Session = Depends(get_db)):
    transfer = db.query(TransferEntity).filter(TransferEntity.transfer_id == transfer_id).first()
    if transfer is None:
        raise HTTPException(status_code=404, detail="Transferencia no encontrada")
    return transfer_detail(db, transfer)
