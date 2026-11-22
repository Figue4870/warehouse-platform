from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from central.config import settings
from central.database import get_db
from central.models import WarehouseEntity
from central.saga import Command, History, TERMINAL, Transfer, enqueue, transition

router = APIRouter(prefix="/transfers", tags=["Transferencias"])


class Request(BaseModel):
    sku: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    source_warehouse_id: str = Field(min_length=1, max_length=20)
    destination_warehouse_id: str = Field(min_length=1, max_length=20)
    quantity: int = Field(gt=0, le=2147483647)
    timeout_seconds: int = Field(60, ge=5, le=3600)
    simulate_failure: Literal["SOURCE_REJECTS", "DESTINATION_REJECTS"] | None = None

    @model_validator(mode="after")
    def validate_request(self):
        if self.source_warehouse_id == self.destination_warehouse_id:
            raise ValueError("Origen y destino deben ser distintos")
        if self.simulate_failure and not settings.SAGA_ALLOW_SIMULATION:
            raise ValueError("La simulación está desactivada")
        return self


def summary(row):
    return {
        "transfer_id": row.code, "sku": row.sku, "quantity": row.quantity,
        "source_warehouse_id": row.source, "destination_warehouse_id": row.destination,
        "state": row.state, "created_at": row.created_at, "finished_at": row.finished_at,
        "status": "COMPLETED" if row.state == "COMPLETED" else
                  "FAILED" if row.state in TERMINAL else "PROCESSING",
    }


@router.post("", status_code=202)
def create(data: Request, response: Response, db: Session = Depends(get_db)):
    for warehouse in (data.source_warehouse_id, data.destination_warehouse_id):
        if db.get(WarehouseEntity, warehouse) is None:
            raise HTTPException(404, f"Bodega desconocida: {warehouse}")
    transfer = Transfer(sku=data.sku, source=data.source_warehouse_id,
                        destination=data.destination_warehouse_id, quantity=data.quantity,
                        timeout_seconds=data.timeout_seconds, simulation=data.simulate_failure,
                        state="REQUESTED")
    db.add(transfer)
    db.flush()
    transition(db, transfer, "REQUESTED")
    enqueue(db, transfer, "RESERVE")
    result = summary(transfer)
    db.commit()
    response.headers["Location"] = f"/transfers/{result['transfer_id']}"
    return result


@router.get("")
def list_transfers(state: str | None = None, sku: str | None = None,
                   limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    query = select(Transfer)
    if state:
        query = query.where(Transfer.state == state)
    if sku:
        query = query.where(Transfer.sku == sku)
    return [summary(row) for row in db.scalars(query.order_by(Transfer.id.desc()).limit(limit))]


@router.get("/{code}")
def detail(code: str, db: Session = Depends(get_db)):
    prefix, _, number = code.partition("-")
    transfer = db.get(Transfer, int(number)) if prefix == "TR" and number.isascii() and number.isdecimal() and len(number) <= 10 else None
    if not transfer or transfer.code != code:
        raise HTTPException(404, "Transferencia desconocida")
    history = db.scalars(select(History).where(History.transfer_id == transfer.id).order_by(History.id))
    commands = db.scalars(select(Command).where(Command.transfer_id == transfer.id).order_by(Command.id))
    return {
        **summary(transfer),
        "history": [{"state": row.state, "note": row.note, "occurred_at": row.occurred_at} for row in history],
        "commands": [{"step": row.step, "warehouse_id": row.warehouse, "published_at": row.published_at} for row in commands],
    }
