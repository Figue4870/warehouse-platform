"""Modelo de lectura de la central (CQRS, lado de consulta)."""

from sqlalchemy import JSON, BigInteger, Boolean, Column, Date, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint

from central.database import Base


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


class WarehouseEntity(Base):
    """Una bodega conocida por la central y qué tan al día está su información."""

    __tablename__ = "warehouses"

    warehouse_id = Column(String(20), primary_key=True)
    api_url = Column(String(200))                         # cómo llamar a la bodega
    # Registro hecho por el administrador global
    nit = Column(String(30), unique=True)
    name = Column(String(200))
    location = Column(String(120))                        # ciudad / región
    address = Column(String(250))
    description = Column(Text)
    capacity_units = Column(Integer)                      # capacidad de almacenamiento
    admin_username = Column(String(100))                  # administrador encargado
    heartbeat_secret = Column(String(120))                # clave del heartbeat de bodegas registradas aquí
    registered_by = Column(String(100))
    registered_at = Column(DateTime(timezone=True))
    catalog_sent_at = Column(DateTime(timezone=True))     # cuándo se le envió el catálogo universal completo
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_heartbeat_at = Column(DateTime(timezone=True))   # último "sigo viva"
    last_heartbeat_attempt_at = Column(DateTime(timezone=True))           # última pregunta de la central
    last_heartbeat_error = Column(Text)                                   # causa de la última falla
    heartbeat_failures = Column(Integer, nullable=False, default=0)       # fallas seguidas
    heartbeat_rejections = Column(Integer, nullable=False, default=0)     # respuestas no auténticas (total)
    last_heartbeat_instance = Column(String(60))                          # instancia de la API que respondió
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
    unit_price = Column(Numeric(14, 2))                    # precio real de la compra o la venta
    counterparty = Column(String(200))
    created_by = Column(String(100))
    lot = Column(String(100))
    reference = Column(String(200))
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    projected_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    exported_at = Column(DateTime(timezone=True))  # cuándo se subió a S3 (NULL = pendiente)


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


# Fase 6: estado de la Saga (no es modelo de lectura; no se reconstruye desde las bodegas)
class TransferEntity(Base):
    """Una transferencia entre dos bodegas y en qué paso de la Saga va."""

    __tablename__ = "transfers"

    id = Column(Integer, primary_key=True)
    transfer_id = Column(String(30), unique=True)          # TR-000001 (se asigna al crearla)
    sku = Column(String(64), nullable=False)
    source_warehouse_id = Column(String(20), nullable=False)
    destination_warehouse_id = Column(String(20), nullable=False)
    quantity = Column(Integer, nullable=False)
    state = Column(String(30), nullable=False)
    compensation_step = Column(String(20))                 # CANCEL_RECEIVE | RELEASE (solo en COMPENSATING)
    failure_reason = Column(Text)
    note = Column(Text)                                    # explicación del estado actual
    timeout_seconds = Column(Integer, nullable=False)
    reserve_deadline = Column(DateTime(timezone=True))
    receive_deadline = Column(DateTime(timezone=True))
    simulate_failure = Column(String(30))
    correlation_id = Column(String(80))
    requested_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at = Column(DateTime(timezone=True))
    exported_at = Column(DateTime(timezone=True))


class TransferHistoryEntity(Base):
    """Cada cambio de estado (diagrama de estados "en vivo"). event_id único = respuesta ya procesada."""

    __tablename__ = "transfer_history"

    id = Column(Integer, primary_key=True)
    transfer_id = Column(String(30), nullable=False, index=True)
    from_state = Column(String(30))
    to_state = Column(String(30), nullable=False)
    event_id = Column(String(36), unique=True)
    event_type = Column(String(60))
    warehouse_id = Column(String(20))
    detail = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class TransferCommandEntity(Base):
    """OUTBOX de comandos de la central (pasos de la Saga y comandos de plataforma como el catálogo)."""

    __tablename__ = "transfer_commands"

    id = Column(Integer, primary_key=True)
    command_id = Column(String(36), unique=True, nullable=False)
    transfer_id = Column(String(30), index=True)
    warehouse_id = Column(String(20), nullable=False)
    command_type = Column(String(40), nullable=False)
    payload = Column(JSON, nullable=False)
    deadline = Column(DateTime(timezone=True))
    correlation_id = Column(String(80))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    published_at = Column(DateTime(timezone=True))        # NULL = pendiente
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)


# Usuarios, roles y claves de firma de tokens
class UserEntity(Base):
    """Usuario del directorio local. Con un directorio externo (LDAP/AD) guarda solo el perfil."""

    __tablename__ = "users"

    username = Column(String(100), primary_key=True)
    display_name = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False)
    warehouse_id = Column(String(20))                      # obligatorio para ADMIN_BODEGA y REGISTRADOR
    password_hash = Column(String(200))
    must_change_password = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_login_at = Column(DateTime(timezone=True))


class SigningKeyEntity(Base):
    """Clave privada con la que la central firma los tokens (compartida por todas las instancias de la API)."""

    __tablename__ = "signing_keys"

    kid = Column(String(40), primary_key=True)
    private_key = Column(String(100), nullable=False)
    public_key = Column(String(100), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


# Catálogo universal y solicitudes de productos
class UniversalProductEntity(Base):
    """Producto universal: lo crea el administrador global y lo reciben todas las bodegas."""

    __tablename__ = "universal_products"

    sku = Column(String(64), primary_key=True)
    name = Column(String(200), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)  # otros nombres universales
    description = Column(Text)
    category = Column(String(100))
    unit = Column(String(30), nullable=False)
    supplier = Column(String(200))
    supplier_cost = Column(Numeric(14, 2))
    retail_price = Column(Numeric(14, 2))
    wholesale_price = Column(Numeric(14, 2))
    is_active = Column(Boolean, nullable=False, default=True)
    catalog_version = Column(Integer, nullable=False, default=1)
    created_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class ProductRequestEntity(Base):
    """Solicitud de producto nuevo que una bodega envió a la central."""

    __tablename__ = "product_requests"
    __table_args__ = (UniqueConstraint("warehouse_id", "request_id", name="uq_product_requests_origin"),)

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    request_id = Column(Integer, nullable=False)            # id de la solicitud en la bodega
    request_code = Column(String(40))
    status = Column(String(20), nullable=False, default="PENDING")   # PENDING | APPROVED | REJECTED
    product = Column(JSON, nullable=False)
    requested_by = Column(String(100))
    approved_by_warehouse = Column(String(100))
    decided_by = Column(String(100))
    decided_at = Column(DateTime(timezone=True))
    decision_note = Column(Text)
    sku = Column(String(64))                                  # SKU creado al aprobar
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class NotificationEntity(Base):
    """Notificaciones del sistema para un rol (y opcionalmente una bodega)."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_audience", "audience_role", "read_at"),)

    id = Column(Integer, primary_key=True)
    audience_role = Column(String(20), nullable=False)
    warehouse_id = Column(String(20))
    severity = Column(String(10), nullable=False, default="INFO")   # INFO | WARNING | CRITICAL
    kind = Column(String(40), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text)
    dedupe_key = Column(String(120), unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    read_at = Column(DateTime(timezone=True))
    read_by = Column(String(100))


# Fase 8: procesos Batch
class BatchRunEntity(Base):
    """Cada ejecución de un proceso Batch (programada, manual o de recuperación)."""

    __tablename__ = "batch_runs"
    __table_args__ = (Index("ix_batch_runs_job_date", "job_name", "business_date"),)

    id = Column(Integer, primary_key=True)
    job_name = Column(String(60), nullable=False)
    business_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False)               # REQUESTED | RUNNING | SUCCEEDED | PARTIAL | FAILED
    trigger = Column(String(20), nullable=False)              # SCHEDULE | MANUAL | CATCH_UP
    requested_by = Column(String(100))
    attempt = Column(Integer, nullable=False, default=1)
    warehouses_processed = Column(Integer)
    movements_processed = Column(Integer)
    stale_warehouses = Column(JSON)
    outputs = Column(JSON)                                    # llaves escritas en S3
    error = Column(Text)
    correlation_id = Column(String(80))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))


# Fase 9: contratos de proveedores (documento en S3; la Lambda lo valida y extrae su metadata)
class ContractEntity(Base):
    __tablename__ = "contracts"

    contract_id = Column(String(20), primary_key=True)        # CT-000001
    title = Column(String(200), nullable=False)
    supplier_nit = Column(String(30))
    supplier_name = Column(String(200))
    supplier_verified = Column(Boolean)                       # confirmado con el sistema de Proveedores
    signed_on = Column(Date)
    valid_until = Column(Date)
    agreed_prices = Column(JSON, nullable=False, default=list)   # [{"sku": ..., "price": "..."}]
    metadata_source = Column(String(20))                      # DOCUMENT | FORM | MIXED
    extracted = Column(JSON)                                  # lo que la Lambda leyó del documento
    document_key = Column(String(300), nullable=False)
    document_name = Column(String(200))
    size_bytes = Column(Integer)
    status = Column(String(20), nullable=False, default="UPLOADED")
    # UPLOADED -> EXTRACTED | INVALID (Lambda) -> APPROVED | REJECTED (Líder) -> TERMINATED
    sha256 = Column(String(64))
    pages = Column(Integer)
    validation_errors = Column(JSON)
    uploaded_by = Column(String(100))
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    processed_at = Column(DateTime(timezone=True))
    reviewed_by = Column(String(100))
    reviewed_at = Column(DateTime(timezone=True))
    review_note = Column(Text)


class StorageEventEntity(Base):
    """Avisos que la Lambda envía a la central (uno por objeto y versión: repetirlo no cambia nada)."""

    __tablename__ = "storage_events"

    event_id = Column(String(120), primary_key=True)          # llave del objeto + etag
    kind = Column(String(40), nullable=False)                 # CONTRACT_PROCESSED | DAILY_REPORT_PUBLISHED
    object_key = Column(String(300), nullable=False)
    outcome = Column(String(20), nullable=False)              # OK | REJECTED
    detail = Column(JSON)
    received_at = Column(DateTime(timezone=True), nullable=False, default=_now)


# Envíos con Servientrega (integración asíncrona)
class ShipmentEntity(Base):
    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True)
    transfer_id = Column(String(30), nullable=False, unique=True)
    status = Column(String(30), nullable=False, default="PENDING")   # PENDING | CREATED | IN_TRANSIT | DELIVERED | ERROR
    tracking_number = Column(String(40))
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)
    next_attempt_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
