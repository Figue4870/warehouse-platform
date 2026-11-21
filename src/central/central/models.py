"""Modelo de lectura de la central (CQRS, lado de consulta)."""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, Numeric, String, Text, UniqueConstraint

from central.database import Base


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


class WarehouseEntity(Base):
    """Una bodega conocida por la central y qué tan al día está su información."""

    __tablename__ = "warehouses"

    warehouse_id = Column(String(20), primary_key=True)
    api_url = Column(String(200))                         # cómo llamar a la bodega (de su heartbeat)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_heartbeat_at = Column(DateTime(timezone=True))   # último "sigo viva"
    last_event_at = Column(DateTime(timezone=True))
    reported_sequence = Column(BigInteger, nullable=False, default=0)    # la bodega dice: publiqué hasta N
    max_received_sequence = Column(BigInteger, nullable=False, default=0)
    watermark_sequence = Column(BigInteger, nullable=False, default=0)   # la central tiene TODO hasta aquí, sin huecos
    watermark_updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    outbox_pending = Column(Integer)
    last_snapshot_at = Column(DateTime(timezone=True))
    last_sync_at = Column(DateTime(timezone=True))
    last_sync_kind = Column(String(20))
    last_sync_status = Column(String(20))
    last_sync_error = Column(Text)


class CatalogProductEntity(Base):
    """Catálogo global por SKU (el SKU es el código universal; el id cambia en cada bodega)."""

    __tablename__ = "catalog_products"

    sku = Column(String(64), primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    category = Column(String(100))
    unit = Column(String(30))
    supplier = Column(String(200))
    supplier_cost = Column(Numeric(14, 2))
    retail_price = Column(Numeric(14, 2))
    wholesale_price = Column(Numeric(14, 2))
    source_warehouse_id = Column(String(20))
    source_updated_at = Column(DateTime(timezone=True))


class WarehouseProductEntity(Base):
    """El producto dentro de una bodega: su id local y si está activo."""

    __tablename__ = "warehouse_products"

    warehouse_id = Column(String(20), primary_key=True)
    sku = Column(String(64), primary_key=True)
    product_id_local = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    product_version = Column(Integer, nullable=False, default=0)


class WarehouseInventoryEntity(Base):
    """Saldo de un SKU en una bodega, según el último evento recibido."""

    __tablename__ = "warehouse_inventory"

    warehouse_id = Column(String(20), primary_key=True)
    sku = Column(String(64), primary_key=True)
    quantity = Column(Integer, nullable=False, default=0)
    reserved_quantity = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=0)  # inventory.version de la bodega
    source_updated_at = Column(DateTime(timezone=True))   # cuándo cambió en la bodega
    projected_at = Column(DateTime(timezone=True), nullable=False, default=_now)  # cuándo lo supo la central


class MovementHistoryEntity(Base):
    """Historial global de movimientos (base de los reportes y de la exportación a S3/Athena)."""

    __tablename__ = "movement_history"

    event_id = Column(String(36), primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    sequence = Column(BigInteger)
    movement_id = Column(Integer)
    sku = Column(String(64), nullable=False)
    product_name = Column(String(200))
    category = Column(String(100))
    type = Column(String(20), nullable=False)
    quantity = Column(Integer, nullable=False)
    quantity_after = Column(Integer)
    reserved_after = Column(Integer)
    unit_retail_price = Column(Numeric(14, 2))
    unit_supplier_cost = Column(Numeric(14, 2))
    lot = Column(String(100))
    reference = Column(String(200))
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    projected_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class CentralAlertEntity(Base):
    __tablename__ = "central_alerts"

    event_id = Column(String(36), primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    alert_type = Column(String(30), nullable=False)
    sku = Column(String(64))
    product_name = Column(String(200))
    message = Column(Text)
    occurred_at = Column(DateTime(timezone=True), nullable=False)


class ProcessedEventEntity(Base):
    """IDEMPOTENCIA: un evento (event_id) se aplica UNA sola vez, llegue por RabbitMQ o por /sync."""

    __tablename__ = "processed_events"
    __table_args__ = (UniqueConstraint("warehouse_id", "sequence", name="uq_processed_warehouse_sequence"),)

    event_id = Column(String(36), primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    sequence = Column(BigInteger)
    event_type = Column(String(60), nullable=False)
    source = Column(String(10), nullable=False)  # stream (RabbitMQ) | pull (/sync/events)
    processed_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class SyncRunEntity(Base):
    """Registro de cada sincronización hecha por el reconciliador o a mano."""

    __tablename__ = "sync_runs"

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    kind = Column(String(20), nullable=False)  # CATCH_UP | SNAPSHOT
    status = Column(String(20), nullable=False)  # OK | ERROR
    events_applied = Column(Integer, nullable=False, default=0)
    detail = Column(Text)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at = Column(DateTime(timezone=True))
