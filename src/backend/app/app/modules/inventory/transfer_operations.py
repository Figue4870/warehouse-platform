from fastapi import APIRouter, Depends, Query
from sqlalchemy import JSON, Column, String, select
from sqlalchemy.orm import Session, aliased

from app.database import Base, get_db


class Operation(Base):
    __tablename__ = "transfer_operations"
    transfer_id = Column(String(24), primary_key=True)
    step = Column(String(20), primary_key=True)
    result = Column(JSON, nullable=False)



def protected_quantity(db, sku):
    end = aliased(Operation)
    operations = db.scalars(select(Operation).where(
        Operation.step == "RESERVE",
        Operation.result["sku"].as_string() == sku,
        Operation.result["outcome"].as_string() == "OK",
        ~select(end.transfer_id).where(
            end.transfer_id == Operation.transfer_id,
            end.step.in_(("RELEASE", "COMMIT")),
            end.result["outcome"].as_string() == "OK",
        ).exists(),
    ))
    return sum(operation.result["quantity"] for operation in operations)



router = APIRouter(tags=["Transferencias"])


@router.get("/transfer-operations")
def list_operations(transfer_id: str | None = None, limit: int = Query(50, ge=1, le=500),
                    db: Session = Depends(get_db)):
    query = select(Operation)
    if transfer_id:
        query = query.where(Operation.transfer_id == transfer_id)
    return [
        {"transfer_id": row.transfer_id, "step": row.step, **row.result}
        for row in db.scalars(query.order_by(Operation.transfer_id.desc(), Operation.step).limit(limit))
    ]
