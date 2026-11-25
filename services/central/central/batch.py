"""Fase 8: reporte diario de todas las bodegas (proceso Batch).

Lee el modelo de lectura de la central (no toca las bases de las bodegas), arma un reporte por bodega, uno
consolidado y el archivo de auditoría del día, y los guarda en S3. Es reejecutable: las llaves dependen solo
de la fecha, así que repetir el día reemplaza los mismos archivos; cada ejecución queda en batch_runs.
"""

import csv
import io
import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from platform_common.auth import ADMIN_GLOBAL, LIDER
from platform_common.correlation import get_correlation_id
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from central.config import settings
from central.models import (
    BatchRunEntity,
    CatalogProductEntity,
    CentralAlertEntity,
    MovementHistoryEntity,
    ProcessedEventEntity,
    WarehouseEntity,
    WarehouseInventoryEntity,
)
from central.notifications import INFO, WARNING, notify
from central.projection import as_aware, now_utc
from central.status import warehouse_status

logger = logging.getLogger("central.batch")

JOB_NAME = "daily-inventory-report"
REQUESTED, RUNNING, SUCCEEDED, PARTIAL, FAILED = "REQUESTED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"
DONE = (SUCCEEDED, PARTIAL)
SCHEDULE, MANUAL, CATCH_UP = "SCHEDULE", "MANUAL", "CATCH_UP"


def zone() -> ZoneInfo:
    return ZoneInfo(settings.BATCH_TIMEZONE)


def day_window(business_date: date) -> tuple[datetime, datetime]:
    """Inicio y fin (UTC) del día en la zona horaria del negocio."""
    start = datetime.combine(business_date, time.min, tzinfo=zone())
    return start.astimezone(ZoneInfo("UTC")), (start + timedelta(days=1)).astimezone(ZoneInfo("UTC"))


def output_keys(business_date: date, warehouse_ids) -> dict:
    d = business_date
    return {
        "warehouses": {w: f"warehouses/{w}/{d:%Y}/{d:%m}/inventory-{d.isoformat()}.json" for w in warehouse_ids},
        "report_json": f"reports/daily/{d:%Y}/{d:%m}/{d.isoformat()}.json",
        "report_csv": f"reports/daily/{d:%Y}/{d:%m}/{d.isoformat()}.csv",
        "audit": f"audit/{d:%Y}/{d:%m}/{d:%d}/events.jsonl",
    }


def _money(value) -> str:
    return f"{Decimal(value or 0):.2f}"


def _closing_stock(db: Session, warehouse_id: str, end: datetime) -> dict:
    """Saldo de cada SKU al cierre del día: el último movimiento antes del cierre o, si no hay, el saldo actual."""
    stock = {inv.sku: (inv.quantity, inv.reserved_quantity)
             for inv in db.query(WarehouseInventoryEntity).filter(WarehouseInventoryEntity.warehouse_id == warehouse_id)}
    latest = (db.query(MovementHistoryEntity.sku, func.max(MovementHistoryEntity.occurred_at))
              .filter(MovementHistoryEntity.warehouse_id == warehouse_id, MovementHistoryEntity.occurred_at < end)
              .group_by(MovementHistoryEntity.sku).all())
    for sku, occurred_at in latest:
        row = (db.query(MovementHistoryEntity)
               .filter(MovementHistoryEntity.warehouse_id == warehouse_id, MovementHistoryEntity.sku == sku,
                       MovementHistoryEntity.occurred_at == occurred_at)
               .order_by(MovementHistoryEntity.sequence.desc()).first())
        if row is not None and row.quantity_after is not None:
            stock[sku] = (row.quantity_after, row.reserved_after or 0)
    return stock


def build_reports(db: Session, business_date: date, run_id: int, now: Optional[datetime] = None) -> dict:
    now = now or now_utc()
    start, end = day_window(business_date)
    warehouses = db.query(WarehouseEntity).order_by(WarehouseEntity.warehouse_id).all()
    catalog = {p.sku: p for p in db.query(CatalogProductEntity).all()}
    movements = (db.query(MovementHistoryEntity)
                 .filter(MovementHistoryEntity.occurred_at >= start, MovementHistoryEntity.occurred_at < end)
                 .order_by(MovementHistoryEntity.warehouse_id, MovementHistoryEntity.occurred_at).all())
    alerts = (db.query(CentralAlertEntity)
              .filter(CentralAlertEntity.occurred_at >= start, CentralAlertEntity.occurred_at < end).all())
    by_warehouse = defaultdict(list)
    for m in movements:
        by_warehouse[m.warehouse_id].append(m)
    alerts_by_warehouse = defaultdict(lambda: defaultdict(int))
    for a in alerts:
        alerts_by_warehouse[a.warehouse_id][a.alert_type] += 1

    reports, csv_rows, stale = {}, [], []
    for warehouse in warehouses:
        wid = warehouse.warehouse_id
        status = warehouse_status(warehouse, now)
        if status["stale"]:
            stale.append(wid)
        by_type = defaultdict(lambda: {"count": 0, "units": 0})
        purchases = {"units": 0, "value": Decimal(0)}
        sales = {"units": 0, "value": Decimal(0)}
        sold_by_sku = defaultdict(int)
        for m in by_warehouse[wid]:
            by_type[m.type]["count"] += 1
            by_type[m.type]["units"] += m.quantity
            if m.type == "RECEIPT":
                purchases["units"] += m.quantity
                purchases["value"] += m.quantity * (m.unit_price if m.unit_price is not None else m.unit_supplier_cost or 0)
            elif m.type == "SHIPMENT":
                sales["units"] += m.quantity
                sales["value"] += m.quantity * (m.unit_price if m.unit_price is not None else m.unit_retail_price or 0)
                sold_by_sku[m.sku] += m.quantity
        closing = _closing_stock(db, wid, end)
        stock_rows = []
        for sku, (quantity, reserved) in sorted(closing.items()):
            product = catalog.get(sku)
            price = product.retail_price if product and product.retail_price is not None else Decimal(0)
            stock_rows.append({"sku": sku, "name": product.name if product else None, "quantity": quantity,
                               "reserved": reserved, "retail_value": _money(price * (quantity + reserved))})
            csv_rows.append([business_date.isoformat(), wid, sku, product.name if product else "", quantity, reserved,
                             sold_by_sku.get(sku, 0), _money(price * (quantity + reserved))])
        reports[wid] = {
            "report": "inventory-daily", "version": 1, "run_id": run_id,
            "warehouse_id": wid, "warehouse_name": warehouse.name, "business_date": business_date.isoformat(),
            "timezone": settings.BATCH_TIMEZONE, "generated_at": now.isoformat(),
            "data_freshness": {"status": status["status"], "stale": status["stale"], "note": status["note"],
                               "watermark_sequence": warehouse.watermark_sequence, "events_missing": status["events_missing"]},
            "movements": {"count": len(by_warehouse[wid]), "by_type": dict(by_type)},
            "purchases": {"units": purchases["units"], "value": _money(purchases["value"])},
            "sales": {"units": sales["units"], "value": _money(sales["value"])},
            "closing_stock": stock_rows,
            "out_of_stock": [r["sku"] for r in stock_rows if r["quantity"] == 0],
            "alerts": dict(alerts_by_warehouse[wid]),
        }

    consolidated = {
        "report": "company-daily", "version": 1, "run_id": run_id, "business_date": business_date.isoformat(),
        "timezone": settings.BATCH_TIMEZONE, "generated_at": now.isoformat(), "stale_warehouses": stale,
        "warehouses": [{"warehouse_id": wid, "movements": r["movements"]["count"], "purchases": r["purchases"],
                        "sales": r["sales"], "units_on_hand": sum(s["quantity"] + s["reserved"] for s in r["closing_stock"]),
                        "out_of_stock": len(r["out_of_stock"]), "stale": wid in stale} for wid, r in reports.items()],
        "totals": {
            "movements": len(movements),
            "purchases_value": _money(sum(Decimal(r["purchases"]["value"]) for r in reports.values())),
            "sales_value": _money(sum(Decimal(r["sales"]["value"]) for r in reports.values())),
            "sales_units": sum(r["sales"]["units"] for r in reports.values()),
        },
    }
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["business_date", "warehouse_id", "sku", "name", "quantity", "reserved", "sold_units", "retail_value"])
    writer.writerows(csv_rows)
    events = (db.query(ProcessedEventEntity)
              .filter(ProcessedEventEntity.processed_at >= start, ProcessedEventEntity.processed_at < end)
              .order_by(ProcessedEventEntity.warehouse_id, ProcessedEventEntity.sequence).all())
    audit = "".join(json.dumps({"event_id": e.event_id, "warehouse_id": e.warehouse_id, "sequence": e.sequence,
                                "event_type": e.event_type, "source": e.source,
                                "processed_at": as_aware(e.processed_at).isoformat()}) + "\n" for e in events)
    return {"warehouses": reports, "consolidated": consolidated, "csv": buffer.getvalue(), "audit": audit,
            "stale": stale, "movements": len(movements)}


def request_run(db: Session, business_date: date, trigger: str, requested_by: Optional[str] = None) -> BatchRunEntity:
    """Deja la ejecución pedida; el worker central_batch la toma. No hace commit."""
    attempt = (db.query(func.count(BatchRunEntity.id))
               .filter(BatchRunEntity.job_name == JOB_NAME, BatchRunEntity.business_date == business_date).scalar() or 0) + 1
    run = BatchRunEntity(job_name=JOB_NAME, business_date=business_date, status=REQUESTED, trigger=trigger,
                         requested_by=requested_by, attempt=attempt, correlation_id=get_correlation_id(), created_at=now_utc())
    db.add(run)
    db.flush()
    return run


def claim_next(session_factory: sessionmaker) -> Optional[int]:
    """Toma la ejecución pedida más antigua (con SKIP LOCKED, dos workers no toman la misma)."""
    with session_factory() as db:
        query = db.query(BatchRunEntity).filter(BatchRunEntity.status == REQUESTED).order_by(BatchRunEntity.id)
        if db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        run = query.first()
        if run is None:
            return None
        run.status, run.started_at = RUNNING, now_utc()
        db.commit()
        return run.id


def execute(session_factory: sessionmaker, storage, run_id: int) -> str:
    with session_factory() as db:
        run = db.get(BatchRunEntity, run_id)
        business_date = run.business_date
    try:
        with session_factory() as db:
            data = build_reports(db, business_date, run_id)
        keys = output_keys(business_date, data["warehouses"])
        for wid, report in data["warehouses"].items():
            storage.put(keys["warehouses"][wid], json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
                        "application/json")
        storage.put(keys["report_csv"], data["csv"].encode("utf-8"), "text/csv")
        storage.put(keys["audit"], data["audit"].encode("utf-8"), "application/x-ndjson")
        # El consolidado va al final: su llegada a S3 dispara la Lambda que publica el reporte
        storage.put(keys["report_json"], json.dumps(data["consolidated"], ensure_ascii=False, indent=2).encode("utf-8"),
                    "application/json")
        status = PARTIAL if data["stale"] else SUCCEEDED
        with session_factory() as db:
            run = db.get(BatchRunEntity, run_id)
            run.status, run.finished_at = status, now_utc()
            run.warehouses_processed, run.movements_processed = len(data["warehouses"]), data["movements"]
            run.stale_warehouses = data["stale"]
            run.outputs = [*keys["warehouses"].values(), keys["report_json"], keys["report_csv"], keys["audit"]]
            note = (f"{len(data['warehouses'])} bodegas, {data['movements']} movimientos"
                    + (f". Datos posiblemente desactualizados: {', '.join(data['stale'])}" if data["stale"] else ""))
            for role in (LIDER, ADMIN_GLOBAL):
                notify(db, role, "BATCH_REPORT", f"Reporte del {business_date.isoformat()} listo", note,
                       WARNING if data["stale"] else INFO, dedupe_key=f"batch:{run_id}:{role}")
            db.commit()
        logger.info("Batch terminado", extra={"event": "batch_finished", "context": {
            "run_id": run_id, "business_date": business_date.isoformat(), "status": status,
            "movements": data["movements"], "stale_warehouses": data["stale"]}})
        return status
    except Exception as error:  # noqa: BLE001 - la ejecución queda FAILED y se puede repetir
        with session_factory() as db:
            run = db.get(BatchRunEntity, run_id)
            run.status, run.finished_at, run.error = FAILED, now_utc(), f"{type(error).__name__}: {error}"[:2000]
            notify(db, ADMIN_GLOBAL, "BATCH_FAILED", f"Falló el reporte del {business_date.isoformat()}", run.error,
                   WARNING, dedupe_key=f"batch-failed:{run_id}")
            db.commit()
        logger.exception("Batch fallido", extra={"event": "batch_failed", "context": {"run_id": run_id}})
        return FAILED


def has_run(db: Session, business_date: date, statuses=(REQUESTED, RUNNING, *DONE)) -> bool:
    return db.query(BatchRunEntity.id).filter(BatchRunEntity.job_name == JOB_NAME, BatchRunEntity.business_date == business_date,
                                              BatchRunEntity.status.in_(statuses)).first() is not None


def automatic_attempts(db: Session, business_date: date) -> int:
    return (db.query(func.count(BatchRunEntity.id))
            .filter(BatchRunEntity.job_name == JOB_NAME, BatchRunEntity.business_date == business_date,
                    BatchRunEntity.trigger != MANUAL).scalar() or 0)


def schedule_due(session_factory: sessionmaker, now: Optional[datetime] = None) -> list[int]:
    """Pide la ejecución de hoy después de la hora programada y la de ayer si no se hizo (máquina apagada).

    Una fecha fallida se reintenta sola hasta BATCH_MAX_ATTEMPTS veces; después solo de forma manual.
    """
    local_now = (now or now_utc()).astimezone(zone())
    today, yesterday = local_now.date(), local_now.date() - timedelta(days=1)
    due = [(yesterday, CATCH_UP)]
    if local_now.time() >= time(settings.BATCH_HOUR, settings.BATCH_MINUTE):
        due.append((today, SCHEDULE))
    requested = []
    with session_factory() as db:
        for business_date, trigger in due:
            if not has_run(db, business_date) and automatic_attempts(db, business_date) < settings.BATCH_MAX_ATTEMPTS:
                requested.append(request_run(db, business_date, trigger).id)
        db.commit()
    return requested


def run_view(run: BatchRunEntity) -> dict:
    duration = (as_aware(run.finished_at) - as_aware(run.started_at)).total_seconds() if run.finished_at and run.started_at else None
    return {"id": run.id, "job_name": run.job_name, "business_date": run.business_date.isoformat(), "status": run.status,
            "trigger": run.trigger, "attempt": run.attempt, "requested_by": run.requested_by,
            "warehouses_processed": run.warehouses_processed, "movements_processed": run.movements_processed,
            "stale_warehouses": run.stale_warehouses or [], "outputs": run.outputs or [], "error": run.error,
            "created_at": as_aware(run.created_at), "started_at": as_aware(run.started_at),
            "finished_at": as_aware(run.finished_at), "duration_seconds": round(duration, 3) if duration is not None else None}
