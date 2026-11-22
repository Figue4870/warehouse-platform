"""Exportación reintentable del modelo de lectura central."""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from sqlalchemy import func, select

from central.saga import TERMINAL, Transfer
from central.models import CatalogProductEntity, MovementHistoryEntity, WarehouseInventoryEntity


def iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Storage:
    def __init__(self, directory, bucket=""):
        self.directory = Path(directory)
        self.bucket = bucket
        self.s3 = None
        if bucket:
            import boto3

            self.s3 = boto3.client("s3")

    def write(self, dataset, day, rows):
        key = f"{dataset}/dt={day}/{uuid4().hex}.json"
        data = "".join(json.dumps(row, default=str, ensure_ascii=False) + "\n" for row in rows)
        if self.s3:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data.encode(), ContentType="application/x-ndjson")
            return
        target = self.directory / key
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, delete=False) as file:
            staged = Path(file.name)
            try:
                file.write(data.encode())
                file.close()
                os.replace(staged, target)
            finally:
                staged.unlink(missing_ok=True)


MOVEMENT_FIELDS = (
    "event_id", "warehouse_id", "sequence", "movement_id", "sku", "product_name", "category",
    "type", "quantity", "quantity_after", "reserved_after", "unit_retail_price",
    "unit_supplier_cost", "lot", "reference",
)


def export_movements(db, storage, limit=1000):
    model = MovementHistoryEntity
    pending = db.scalars(
        select(model).where(model.exported_at.is_(None))
        .order_by(model.occurred_at, model.event_id).limit(limit).with_for_update(skip_locked=True)
    ).all()
    groups = defaultdict(list)
    for movement in pending:
        timestamp = iso(movement.occurred_at)
        row = {field: getattr(movement, field) for field in MOVEMENT_FIELDS}
        groups[timestamp[:10]].append(({**row, "occurred_at": timestamp}, movement))
    for day, group in groups.items():
        storage.write("movements", day, [row for row, _ in group])
        for _, movement in group:
            movement.exported_at = datetime.now(timezone.utc)
    db.commit()
    return len(pending)


def snapshot_inventory(db, storage):
    timestamp = iso(datetime.now(timezone.utc))
    inventory, catalog = WarehouseInventoryEntity, CatalogProductEntity
    records = db.execute(
        select(inventory, catalog).outerjoin(catalog, inventory.sku == catalog.sku)
    )
    rows = [
        {
            "snapshot_at": timestamp, "warehouse_id": stock.warehouse_id, "sku": stock.sku,
            "quantity": stock.quantity, "reserved_quantity": stock.reserved_quantity,
            "version": stock.version, "product_name": product.name if product else None,
            "category": product.category if product else None,
            "unit_supplier_cost": product.supplier_cost if product else None,
            "unit_retail_price": product.retail_price if product else None,
        }
        for stock, product in records
    ]
    storage.write("inventory", timestamp[:10], rows)


def export_status(db):
    model = MovementHistoryEntity
    total, exported = db.execute(select(func.count(model.event_id), func.count(model.exported_at))).one()
    completed, shipped = db.execute(select(func.count(Transfer.id), func.count(Transfer.exported_at))
                                    .where(Transfer.state.in_(TERMINAL))).one()
    return {"movements": {"exported": exported, "pending": total - exported},
            "transfers": {"exported": shipped, "pending": completed - shipped}}


def export_transfers(db, storage, limit=1000):
    pending = db.scalars(select(Transfer).where(
        Transfer.state.in_(TERMINAL), Transfer.exported_at.is_(None),
    ).order_by(Transfer.id).limit(limit).with_for_update(skip_locked=True)).all()
    groups = defaultdict(list)
    for row in pending:
        timestamp = iso(row.finished_at)
        groups[timestamp[:10]].append({
            "transfer_id": row.code, "sku": row.sku, "source": row.source, "destination": row.destination,
            "quantity": row.quantity, "state": row.state, "created_at": iso(row.created_at),
            "finished_at": timestamp, "duration_seconds": (row.finished_at - row.created_at).total_seconds(),
        })
    for day, rows in groups.items():
        storage.write("transfers", day, rows)
    for row in pending:
        row.exported_at = datetime.now(timezone.utc)
    db.commit()
    return len(pending)
