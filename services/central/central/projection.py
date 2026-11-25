"""Proyección (CQRS): cómo cada evento de una bodega actualiza el modelo de lectura."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from platform_common.events import EventEnvelope, InvalidEventError
from sqlalchemy.orm import Session

from platform_common.auth import ADMIN_GLOBAL

from central.models import (
    CatalogProductEntity,
    CentralAlertEntity,
    MovementHistoryEntity,
    ProcessedEventEntity,
    ProductRequestEntity,
    WarehouseEntity,
    WarehouseInventoryEntity,
    WarehouseProductEntity,
)
from central.notifications import CRITICAL, INFO, WARNING, notify

APPLIED, DUPLICATE, IGNORED = "applied", "duplicate", "ignored"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite devuelve fechas sin zona horaria; las tratamos como UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def money(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


# bodegas
def get_or_create_warehouse(db: Session, warehouse_id: str, lock: bool = False) -> WarehouseEntity:
    query = db.query(WarehouseEntity).filter(WarehouseEntity.warehouse_id == warehouse_id)
    if lock:
        # Serializa los eventos de UNA bodega (dos proyectores no avanzan el watermark a la vez)
        query = query.with_for_update()
    warehouse = query.first()
    if warehouse is None:
        warehouse = WarehouseEntity(warehouse_id=warehouse_id, first_seen_at=now_utc(), watermark_updated_at=now_utc(),
                                    reported_sequence=0, max_received_sequence=0, watermark_sequence=0)
        db.add(warehouse)
        db.flush()
    return warehouse


def record_heartbeat(db: Session, warehouse_id: str, status: dict, api_url: Optional[str] = None) -> None:
    """Respuesta auténtica: la bodega queda en contacto y se guarda el estado que reportó."""
    warehouse = get_or_create_warehouse(db, warehouse_id, lock=True)
    now = now_utc()
    warehouse.last_heartbeat_at = now
    warehouse.last_heartbeat_attempt_at = now
    warehouse.last_heartbeat_error = None
    warehouse.heartbeat_failures = 0
    warehouse.last_heartbeat_instance = (str(status.get("instance") or "")[:60]) or None
    warehouse.reported_sequence = max(warehouse.reported_sequence or 0, int(status.get("last_sequence") or 0))
    warehouse.outbox_pending = status.get("outbox_pending")
    if api_url:
        warehouse.api_url = api_url.rstrip("/")


def record_heartbeat_failure(db: Session, warehouse_id: str, error: str, rejected: bool, api_url: Optional[str] = None) -> None:
    """Consulta fallida: guarda la causa y cuenta los fallos seguidos y las respuestas rechazadas."""
    warehouse = get_or_create_warehouse(db, warehouse_id, lock=True)
    warehouse.last_heartbeat_attempt_at = now_utc()
    warehouse.last_heartbeat_error = error[:500]
    warehouse.heartbeat_failures = (warehouse.heartbeat_failures or 0) + 1
    if rejected:
        warehouse.heartbeat_rejections = (warehouse.heartbeat_rejections or 0) + 1
    if api_url:
        warehouse.api_url = api_url.rstrip("/")


def advance_watermark(db: Session, warehouse: WarehouseEntity) -> None:
    """Sube el watermark mientras las secuencias sigan seguidas (sin huecos)."""
    following = (
        db.query(ProcessedEventEntity.sequence)
        .filter(ProcessedEventEntity.warehouse_id == warehouse.warehouse_id,
                ProcessedEventEntity.sequence > warehouse.watermark_sequence)
        .order_by(ProcessedEventEntity.sequence)
        .limit(5000)
        .all()
    )
    moved = False
    for (sequence,) in following:
        if sequence != warehouse.watermark_sequence + 1:
            break  # hueco: falta warehouse.watermark_sequence + 1
        warehouse.watermark_sequence = sequence
        moved = True
    if moved:
        warehouse.watermark_updated_at = now_utc()


# catálogo e inventario
def upsert_catalog(db: Session, warehouse_id: str, payload: dict, occurred_at: datetime) -> None:
    sku = payload["sku"]
    product = db.get(CatalogProductEntity, sku)
    if product is None:
        product = CatalogProductEntity(sku=sku, name=payload.get("name") or payload.get("product_name") or sku)
        db.add(product)
        db.flush()
    elif product.source_updated_at and as_aware(product.source_updated_at) > occurred_at:
        return  # ya tenemos datos más nuevos de este SKU
    for field in ("name", "description", "category", "unit", "supplier"):
        if payload.get(field) is not None:
            setattr(product, field, payload[field])
    for field in ("supplier_cost", "retail_price", "wholesale_price"):
        if field in payload:
            setattr(product, field, money(payload[field]))
    product.source_warehouse_id = warehouse_id
    product.source_updated_at = occurred_at


def upsert_warehouse_product(db: Session, warehouse_id: str, payload: dict) -> None:
    key = {"warehouse_id": warehouse_id, "sku": payload["sku"]}
    row = db.get(WarehouseProductEntity, key)
    version = int(payload.get("product_version") or 0)
    if row is None:
        row = WarehouseProductEntity(**key, product_id_local=payload["product_id"], is_active=payload.get("is_active", True), product_version=version)
        db.add(row)
        db.flush()
    elif version > (row.product_version or 0):
        row.product_id_local = payload["product_id"]
        row.is_active = payload.get("is_active", True)
        row.product_version = version


def upsert_inventory(db: Session, warehouse_id: str, sku: str, quantity: int, reserved: int, version: int,
                     source_updated_at: Optional[datetime]) -> bool:
    """Aplica el saldo SOLO si su versión es más nueva. Devuelve True si cambió algo."""
    key = {"warehouse_id": warehouse_id, "sku": sku}
    row = db.get(WarehouseInventoryEntity, key)
    if row is None:
        row = WarehouseInventoryEntity(**key, quantity=0, reserved_quantity=0, version=-1, projected_at=now_utc())
        db.add(row)
        db.flush()
    if version <= (row.version if row.version is not None else -1):
        return False  # evento viejo o repetido: no pisa un saldo más nuevo
    row.quantity = quantity
    row.reserved_quantity = reserved
    row.version = version
    row.source_updated_at = source_updated_at
    row.projected_at = now_utc()
    return True


# un handler por tipo de evento
def _on_product(db: Session, event: EventEnvelope, warehouse_id: str, occurred_at: datetime) -> None:
    upsert_warehouse_product(db, warehouse_id, event.payload)
    upsert_catalog(db, warehouse_id, event.payload, occurred_at)


def _on_movement(db: Session, event: EventEnvelope, warehouse_id: str, occurred_at: datetime) -> None:
    p = event.payload
    if db.get(WarehouseProductEntity, {"warehouse_id": warehouse_id, "sku": p["sku"]}) is None:
        upsert_warehouse_product(db, warehouse_id, {"sku": p["sku"], "product_id": p["product_id"], "product_version": 0})
    if db.get(CatalogProductEntity, p["sku"]) is None:
        upsert_catalog(db, warehouse_id, {"sku": p["sku"], "name": p.get("product_name"), "category": p.get("category"),
                                           "unit": p.get("unit")}, occurred_at)
    upsert_inventory(db, warehouse_id, p["sku"], int(p["quantity_after"]), int(p["reserved_after"]),
                     int(p.get("inventory_version") or 0), parse_time(p.get("created_at")) or occurred_at)
    if db.get(MovementHistoryEntity, event.event_id) is None:
        db.add(MovementHistoryEntity(
            event_id=event.event_id, warehouse_id=warehouse_id, sequence=event.sequence, movement_id=p.get("movement_id"),
            sku=p["sku"], product_name=p.get("product_name"), category=p.get("category"), type=p["type"],
            quantity=int(p["quantity"]), quantity_after=p.get("quantity_after"), reserved_after=p.get("reserved_after"),
            unit_retail_price=money(p.get("retail_price")), unit_supplier_cost=money(p.get("supplier_cost")),
            unit_price=money(p.get("unit_price")), counterparty=p.get("counterparty"), created_by=p.get("created_by"),
            lot=p.get("lot"), reference=p.get("reference"),
            occurred_at=parse_time(p.get("created_at")) or occurred_at,
        ))


ALERT_SEVERITY = {"OUT_OF_STOCK": WARNING, "DISCREPANCY": WARNING, "INCONSISTENCY": CRITICAL}


def _on_alert(db: Session, event: EventEnvelope, warehouse_id: str, occurred_at: datetime) -> None:
    p = event.payload
    if db.get(CentralAlertEntity, event.event_id) is None:
        alert_type = p.get("alert_type") or event.event_type
        db.add(CentralAlertEntity(event_id=event.event_id, warehouse_id=warehouse_id, alert_type=alert_type,
                                  sku=p.get("sku"), product_name=p.get("product_name"), message=p.get("message"), occurred_at=occurred_at))
        notify(db, ADMIN_GLOBAL, f"ALERT_{alert_type}", f"{warehouse_id}: {p.get('product_name') or p.get('sku')}",
               p.get("message") or "", ALERT_SEVERITY.get(alert_type, INFO), warehouse_id, dedupe_key=f"alert:{event.event_id}")


def _on_product_request(db: Session, event: EventEnvelope, warehouse_id: str, occurred_at: datetime) -> None:
    p = event.payload
    exists = (db.query(ProductRequestEntity.id)
              .filter(ProductRequestEntity.warehouse_id == warehouse_id, ProductRequestEntity.request_id == p["request_id"]).first())
    if exists:
        return
    product = p.get("product") or {}
    db.add(ProductRequestEntity(warehouse_id=warehouse_id, request_id=int(p["request_id"]), request_code=p.get("request_code"),
                                status="PENDING", product=product, requested_by=p.get("requested_by"),
                                approved_by_warehouse=p.get("approved_by"), created_at=occurred_at))
    notify(db, ADMIN_GLOBAL, "PRODUCT_REQUEST", f"{warehouse_id} pide crear «{product.get('name', 'producto')}»",
           product.get("reason") or "", INFO, warehouse_id, dedupe_key=f"product-request:{warehouse_id}:{p['request_id']}")


HANDLERS = {
    "PRODUCT_CREATED": _on_product,
    "PRODUCT_UPDATED": _on_product,
    "PRODUCT_DEACTIVATED": _on_product,
    "INVENTORY_MOVEMENT_RECORDED": _on_movement,
    "STOCK_DEPLETED": _on_alert,
    "STOCK_DISCREPANCY": _on_alert,
    "INVENTORY_INCONSISTENCY": _on_alert,
    "PRODUCT_REQUEST_SUBMITTED": _on_product_request,
}


def apply_event(db: Session, event: EventEnvelope, source: str = "stream") -> str:
    """Aplica un evento al modelo de lectura. NO hace commit (lo hace quien llama)."""
    warehouse_id = event.warehouse_id or event.payload.get("warehouse_id")
    if not warehouse_id:
        raise InvalidEventError("El evento no dice de qué bodega viene")

    # El heartbeat solo se acepta cifrado, por POST /sync/heartbeat
    if event.event_type == "WAREHOUSE_HEARTBEAT":
        return IGNORED

    warehouse = get_or_create_warehouse(db, warehouse_id, lock=True)
    if db.get(ProcessedEventEntity, event.event_id) is not None:
        return DUPLICATE

    occurred_at = parse_time(event.occurred_at) or now_utc()
    handler = HANDLERS.get(event.event_type)
    if handler is not None:
        handler(db, event, warehouse_id, occurred_at)
    # Los tipos que la central no proyecta (ej. REPORT_REQUESTED) igual se registran:
    # cuentan para la secuencia y así no parecen "perdidos".
    db.add(ProcessedEventEntity(event_id=event.event_id, warehouse_id=warehouse_id, sequence=event.sequence,
                                event_type=event.event_type, source=source, processed_at=now_utc()))
    if warehouse.last_event_at is None or as_aware(warehouse.last_event_at) < occurred_at:
        warehouse.last_event_at = occurred_at
    if event.sequence:
        warehouse.max_received_sequence = max(warehouse.max_received_sequence or 0, event.sequence)
        db.flush()
        advance_watermark(db, warehouse)
    return APPLIED


def apply_snapshot(db: Session, warehouse_id: str, snapshot: dict) -> int:
    """Resincronización total."""
    warehouse = get_or_create_warehouse(db, warehouse_id, lock=True)
    taken_at = parse_time(snapshot.get("taken_at")) or now_utc()
    changed = 0
    for product in snapshot.get("products", []):
        upsert_warehouse_product(db, warehouse_id, product)
        upsert_catalog(db, warehouse_id, product, parse_time(product.get("updated_at")) or taken_at)
        if upsert_inventory(db, warehouse_id, product["sku"], int(product["quantity"]), int(product["reserved_quantity"]),
                            int(product.get("inventory_version") or 0), parse_time(product.get("inventory_updated_at")) or taken_at):
            changed += 1
    warehouse.reported_sequence = max(warehouse.reported_sequence or 0, int(snapshot.get("last_sequence") or 0))
    warehouse.last_snapshot_at = now_utc()
    return changed
