"""Estado persistente y outbox de comandos de transferencias."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, select

from central.database import Base
from central.models import CatalogProductEntity


def now():
    return datetime.now(timezone.utc)


class Transfer(Base):
    __tablename__ = "transfers"
    id = Column(Integer, primary_key=True)
    sku = Column(String(64), nullable=False)
    source = Column(String(20), nullable=False)
    destination = Column(String(20), nullable=False)
    quantity = Column(Integer, nullable=False)
    state = Column(String(30), nullable=False)
    timeout_seconds = Column(Integer, nullable=False)
    simulation = Column(String(24))
    deadline = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    finished_at = Column(DateTime(timezone=True))
    exported_at = Column(DateTime(timezone=True))

    @property
    def code(self):
        return f"TR-{self.id:06d}"


class History(Base):
    __tablename__ = "transfer_history"
    id = Column(Integer, primary_key=True)
    transfer_id = Column(ForeignKey("transfers.id"), nullable=False, index=True)
    event_id = Column(String(36), unique=True)
    state = Column(String(30), nullable=False)
    note = Column(String)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=now)


class Command(Base):
    __tablename__ = "transfer_commands"
    __table_args__ = (UniqueConstraint("transfer_id", "step"),)
    id = Column(Integer, primary_key=True)
    transfer_id = Column(ForeignKey("transfers.id"), nullable=False, index=True)
    warehouse = Column(String(20), nullable=False)
    step = Column(String(20), nullable=False)
    payload = Column(JSON, nullable=False)
    published_at = Column(DateTime(timezone=True))


TERMINAL = {"COMPLETED", "FAILED", "RESERVATION_RELEASED"}


def transition(db, transfer, state, note=None):
    transfer.state = state
    transfer.deadline = None
    if state in TERMINAL:
        transfer.finished_at = now()
    db.add(History(transfer_id=transfer.id, state=state, note=note))


def enqueue(db, transfer, step):
    if db.scalar(select(Command.id).where(Command.transfer_id == transfer.id, Command.step == step)):
        return
    warehouse = transfer.destination if step in {"RECEIVE", "CANCEL_RECEIVE"} else transfer.source
    payload = {
        "transfer_id": transfer.code, "warehouse_id": warehouse, "step": step,
        "sku": transfer.sku, "quantity": transfer.quantity, "simulate_failure": transfer.simulation,
    }
    if step in {"RESERVE", "RECEIVE"}:
        transfer.deadline = now() + timedelta(seconds=transfer.timeout_seconds)
        payload["deadline"] = transfer.deadline.isoformat()
    if step == "RECEIVE":
        product = db.get(CatalogProductEntity, transfer.sku)
        if product:
            fields = ("name", "description", "category", "unit", "supplier",
                      "supplier_cost", "retail_price", "wholesale_price")
            payload["product"] = {
                field: str(value) if field.endswith(("cost", "price")) and value is not None else value
                for field in fields if (value := getattr(product, field)) is not None
            }
    db.add(Command(transfer_id=transfer.id, warehouse=warehouse, step=step, payload=payload))


def check_timeouts(db):
    cutoff = now() - timedelta(seconds=5)
    transfers = db.scalars(select(Transfer).where(
        Transfer.deadline <= cutoff,
        Transfer.state.in_(("REQUESTED", "RESERVING_SOURCE", "IN_TRANSIT")),
    ).with_for_update(skip_locked=True)).all()
    for transfer in transfers:
        step = "CANCEL_RECEIVE" if transfer.state == "IN_TRANSIT" else "RELEASE"
        transition(db, transfer, "COMPENSATING", "Plazo vencido")
        enqueue(db, transfer, step)


def handle_reply(db, event):
    payload = event.payload
    transfer = db.scalar(select(Transfer).where(
        Transfer.id == int(payload["transfer_id"].removeprefix("TR-"))
    ).with_for_update())
    if transfer is None or db.scalar(select(History.id).where(History.event_id == event.event_id)):
        return
    step, outcome = payload["step"], payload["outcome"]
    command = db.scalar(select(Command).where(Command.transfer_id == transfer.id, Command.step == step))
    db.add(History(transfer_id=transfer.id, event_id=event.event_id,
                   state=transfer.state, note=payload.get("reason") or f"{step}: {outcome}"))
    if command is None or command.warehouse != event.warehouse_id:
        return

    state = transfer.state
    if step == "RESERVE" and state in {"REQUESTED", "RESERVING_SOURCE"}:
        if outcome == "OK":
            transition(db, transfer, "SOURCE_RESERVED")
            transition(db, transfer, "IN_TRANSIT")
            enqueue(db, transfer, "RECEIVE")
        else:
            transition(db, transfer, "FAILED", payload.get("reason"))
    elif step == "RECEIVE" and state == "IN_TRANSIT":
        if outcome == "OK":
            transition(db, transfer, "DESTINATION_RECEIVED")
            enqueue(db, transfer, "COMMIT")
        else:
            transition(db, transfer, "COMPENSATING", payload.get("reason"))
            enqueue(db, transfer, "RELEASE")
    elif step == "CANCEL_RECEIVE" and state == "COMPENSATING":
        if outcome == "ALREADY_RECEIVED":
            transition(db, transfer, "DESTINATION_RECEIVED")
            enqueue(db, transfer, "COMMIT")
        elif outcome == "OK":
            enqueue(db, transfer, "RELEASE")
    elif step == "RELEASE" and state == "COMPENSATING":
        if outcome in {"OK", "NOOP"}:
            transition(db, transfer, "RESERVATION_RELEASED" if outcome == "OK" else "FAILED")
    elif step == "COMMIT" and state == "DESTINATION_RECEIVED" and outcome == "OK":
        transition(db, transfer, "COMPLETED")
