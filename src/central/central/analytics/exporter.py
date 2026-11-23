"""Exportador para Athena: sube a S3 (JSON por líneas, una carpeta por día) lo que aún no se ha exportado."""

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from central.models import (
    CatalogProductEntity,
    MovementHistoryEntity,
    TransferEntity,
    WarehouseEntity,
    WarehouseInventoryEntity,
)
from central.projection import as_aware
from central.saga import TERMINAL, public_status
from central.status import warehouse_status

logger = logging.getLogger("central.exporter")

TABLES = {name: spec for name, spec in json.loads((Path(__file__).parent / "tables.json").read_text(encoding="utf-8")).items()
          if not name.startswith("_")}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value) -> str | None:
    """2026-11-16T20:15:00.123Z (milisegundos y UTC: lo entiende from_iso8601_timestamp de Athena)."""
    if value is None:
        return None
    value = as_aware(value).astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def day(value) -> str:
    return as_aware(value).astimezone(timezone.utc).strftime("%Y-%m-%d")


def money(value) -> str | None:
    return f"{value:.2f}" if value is not None else None


# filas (deben coincidir con tables.json)
def movement_row(m: MovementHistoryEntity) -> dict:
    return {
        "event_id": m.event_id, "warehouse_id": m.warehouse_id, "sequence": m.sequence, "movement_id": m.movement_id,
        "sku": m.sku, "product_name": m.product_name, "category": m.category, "type": m.type, "quantity": m.quantity,
        "quantity_after": m.quantity_after, "reserved_after": m.reserved_after,
        "unit_retail_price": money(m.unit_retail_price), "unit_supplier_cost": money(m.unit_supplier_cost),
        "lot": m.lot, "reference": m.reference, "occurred_at": iso(m.occurred_at),
    }


def transfer_row(t: TransferEntity) -> dict:
    duration = (as_aware(t.finished_at) - as_aware(t.created_at)).total_seconds() if t.finished_at else None
    return {
        "transfer_id": t.transfer_id, "sku": t.sku, "source_warehouse_id": t.source_warehouse_id,
        "destination_warehouse_id": t.destination_warehouse_id, "quantity": t.quantity, "final_state": t.state,
        "status": public_status(t.state), "failure_reason": t.failure_reason,
        "created_at": iso(t.created_at), "finished_at": iso(t.finished_at),
        "duration_seconds": round(duration, 3) if duration is not None else None,
    }


DATASETS = {
    # nombre: (modelo, fila, fecha para la partición, filtro extra)
    "movements": (MovementHistoryEntity, movement_row, lambda m: m.occurred_at, None),
    "transfers": (TransferEntity, transfer_row, lambda t: t.created_at, TransferEntity.state.in_(sorted(TERMINAL))),
}


def to_jsonl(rows: list[dict]) -> bytes:
    return ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode("utf-8")


def _pending(db: Session, dataset: str):
    model, _, _, extra_filter = DATASETS[dataset]
    query = db.query(model).filter(model.exported_at.is_(None))
    return query.filter(extra_filter) if extra_filter is not None else query


def export_dataset(session_factory: sessionmaker, storage, dataset: str, batch_size: int = 5000) -> int:
    """Sube las filas pendientes y SOLO después las marca como exportadas. Devuelve cuántas subió.

    Si S3 falla, no se marca nada y se reintenta en la próxima vuelta. Si el exportador se cae justo
    después de subir, esas filas se suben otra vez: los reportes usan DISTINCT, así que no se duplican.
    """
    model, to_row, date_of, _ = DATASETS[dataset]
    with session_factory() as db:
        records = _pending(db, dataset).limit(batch_size).all()
        if not records:
            return 0
        by_day: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            by_day[day(date_of(record))].append(to_row(record))
        name = uuid.uuid4().hex
        for dt, rows in sorted(by_day.items()):
            storage.put(f"{dataset}/dt={dt}/{name}.json", to_jsonl(rows))
        now = now_utc()
        for record in records:
            record.exported_at = now
        db.commit()
        return len(records)


# foto del inventario
def export_inventory_snapshot(session_factory: sessionmaker, storage, now: datetime | None = None) -> int:
    now = now or now_utc()
    with session_factory() as db:
        warehouses = {w.warehouse_id: warehouse_status(w, now)["status"] for w in db.query(WarehouseEntity).all()}
        rows = (db.query(WarehouseInventoryEntity, CatalogProductEntity)
                .outerjoin(CatalogProductEntity, CatalogProductEntity.sku == WarehouseInventoryEntity.sku)
                .order_by(WarehouseInventoryEntity.warehouse_id, WarehouseInventoryEntity.sku).all())
        snapshot_at = iso(now)
        data = [{
            "snapshot_at": snapshot_at, "warehouse_id": inv.warehouse_id, "sku": inv.sku,
            "product_name": product.name if product else None, "category": product.category if product else None,
            "quantity": inv.quantity, "reserved_quantity": inv.reserved_quantity,
            "retail_price": money(product.retail_price) if product else None,
            "supplier_cost": money(product.supplier_cost) if product else None,
            "warehouse_status": warehouses.get(inv.warehouse_id),
        } for inv, product in rows]
        if data:
            storage.put(f"inventory/dt={day(now)}/snapshot-{now.strftime('%H%M%S')}.json", to_jsonl(data))
        return len(data)


class Exporter:
    def __init__(self, session_factory: sessionmaker, storage, batch_size: int = 5000, snapshot_seconds: int = 3600):
        self.session_factory = session_factory
        self.storage = storage
        self.batch_size = batch_size
        self.snapshot_seconds = snapshot_seconds
        self.last_snapshot_at = None  # la primera vuelta siempre saca una foto del inventario

    def run_once(self, now: datetime | None = None) -> dict:
        now = now or now_utc()
        result = {name: export_dataset(self.session_factory, self.storage, name, self.batch_size) for name in DATASETS}
        if self.last_snapshot_at is None or (now - self.last_snapshot_at).total_seconds() >= self.snapshot_seconds:
            result["inventory"] = export_inventory_snapshot(self.session_factory, self.storage, now)
            self.last_snapshot_at = now
        return result


def export_status(db: Session) -> dict:
    """Para la API: cuánto se ha exportado y cuánto falta."""
    status = {}
    for name, (model, _, _, _) in DATASETS.items():
        status[name] = {"pending_rows": _pending(db, name).count(),
                        "exported_rows": db.query(model).filter(model.exported_at.isnot(None)).count()}
    last = db.query(func.max(MovementHistoryEntity.exported_at)).scalar()
    status["last_export_at"] = as_aware(last) if last else None
    return status
