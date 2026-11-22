-- Base de datos de la central (Fase 7): modelo de lectura (CQRS).
-- Se puede reconstruir desde las bodegas (snapshots + /sync/events).
BEGIN;

-- Bodegas conocidas y qué tan al día está la central con cada una
CREATE TABLE IF NOT EXISTS warehouses (
    warehouse_id           VARCHAR(20)  PRIMARY KEY,
    api_url                VARCHAR(200),
    first_seen_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_heartbeat_at      TIMESTAMPTZ,
    last_event_at          TIMESTAMPTZ,
    reported_sequence      BIGINT       NOT NULL DEFAULT 0,  -- la bodega dice: publiqué hasta N
    max_received_sequence  BIGINT       NOT NULL DEFAULT 0,
    watermark_sequence     BIGINT       NOT NULL DEFAULT 0,  -- la central tiene TODO hasta aquí (sin huecos)
    watermark_updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    outbox_pending         INTEGER,
    last_snapshot_at       TIMESTAMPTZ,
    last_sync_at           TIMESTAMPTZ,
    last_sync_kind         VARCHAR(20),
    last_sync_status       VARCHAR(20),
    last_sync_error        TEXT
);

-- Catálogo global por SKU (código universal)
CREATE TABLE IF NOT EXISTS catalog_products (
    sku                  VARCHAR(64)  PRIMARY KEY,
    name                 VARCHAR(200) NOT NULL,
    description          TEXT,
    category             VARCHAR(100),
    unit                 VARCHAR(30),
    supplier             VARCHAR(200),
    supplier_cost        NUMERIC(14, 2),
    retail_price         NUMERIC(14, 2),
    wholesale_price      NUMERIC(14, 2),
    source_warehouse_id  VARCHAR(20),
    source_updated_at    TIMESTAMPTZ
);

-- Producto dentro de cada bodega (su id local cambia de una bodega a otra)
CREATE TABLE IF NOT EXISTS warehouse_products (
    warehouse_id      VARCHAR(20) NOT NULL,
    sku               VARCHAR(64) NOT NULL,
    product_id_local  INTEGER     NOT NULL,
    is_active         BOOLEAN     NOT NULL DEFAULT TRUE,
    product_version   INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (warehouse_id, sku)
);

-- Saldo de cada SKU en cada bodega (solo se aplica un evento si su versión es más nueva)
CREATE TABLE IF NOT EXISTS warehouse_inventory (
    warehouse_id       VARCHAR(20) NOT NULL,
    sku                VARCHAR(64) NOT NULL,
    quantity           INTEGER     NOT NULL DEFAULT 0,
    reserved_quantity  INTEGER     NOT NULL DEFAULT 0,
    version            INTEGER     NOT NULL DEFAULT 0,
    source_updated_at  TIMESTAMPTZ,              -- cuándo cambió en la bodega
    projected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),  -- cuándo lo supo la central
    PRIMARY KEY (warehouse_id, sku)
);
CREATE INDEX IF NOT EXISTS ix_warehouse_inventory_sku ON warehouse_inventory (sku);

-- Historial global de movimientos (reportes y exportación a S3/Athena)
CREATE TABLE IF NOT EXISTS movement_history (
    event_id            VARCHAR(36)  PRIMARY KEY,
    warehouse_id        VARCHAR(20)  NOT NULL,
    sequence            BIGINT,
    movement_id         INTEGER,
    sku                 VARCHAR(64)  NOT NULL,
    product_name        VARCHAR(200),
    category            VARCHAR(100),
    type                VARCHAR(20)  NOT NULL,
    quantity            INTEGER      NOT NULL,
    quantity_after      INTEGER,
    reserved_after      INTEGER,
    unit_retail_price   NUMERIC(14, 2),
    unit_supplier_cost  NUMERIC(14, 2),
    lot                 VARCHAR(100),
    reference           VARCHAR(200),
    occurred_at         TIMESTAMPTZ  NOT NULL,
    projected_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    exported_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_movement_history_wh_time ON movement_history (warehouse_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_movement_history_sku ON movement_history (sku);

CREATE TABLE IF NOT EXISTS central_alerts (
    event_id      VARCHAR(36)  PRIMARY KEY,
    warehouse_id  VARCHAR(20)  NOT NULL,
    alert_type    VARCHAR(30)  NOT NULL,
    sku           VARCHAR(64),
    product_name  VARCHAR(200),
    message       TEXT,
    occurred_at   TIMESTAMPTZ  NOT NULL
);

-- IDEMPOTENCIA: cada evento se aplica una sola vez (llegue por RabbitMQ o por /sync)
CREATE TABLE IF NOT EXISTS processed_events (
    event_id      VARCHAR(36)  PRIMARY KEY,
    warehouse_id  VARCHAR(20)  NOT NULL,
    sequence      BIGINT,
    event_type    VARCHAR(60)  NOT NULL,
    source        VARCHAR(10)  NOT NULL,      -- stream | pull
    processed_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_processed_warehouse_sequence UNIQUE (warehouse_id, sequence)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id              SERIAL       PRIMARY KEY,
    warehouse_id    VARCHAR(20)  NOT NULL,
    kind            VARCHAR(20)  NOT NULL,    -- CATCH_UP | SNAPSHOT
    status          VARCHAR(20)  NOT NULL,    -- OK | ERROR
    events_applied  INTEGER      NOT NULL DEFAULT 0,
    detail          TEXT,
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

COMMIT;

CREATE INDEX IF NOT EXISTS ix_movement_history_pending_export
    ON movement_history (occurred_at, event_id) WHERE exported_at IS NULL;

BEGIN;
CREATE TABLE IF NOT EXISTS transfers (
    id SERIAL PRIMARY KEY,
    sku VARCHAR(64) NOT NULL,
    source VARCHAR(20) NOT NULL,
    destination VARCHAR(20) NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    state VARCHAR(30) NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    simulation VARCHAR(24),
    deadline TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    exported_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS transfer_history (
    id SERIAL PRIMARY KEY,
    transfer_id INTEGER NOT NULL REFERENCES transfers(id),
    event_id VARCHAR(36) UNIQUE,
    state VARCHAR(30) NOT NULL,
    note VARCHAR,
    occurred_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transfer_history_transfer_id ON transfer_history(transfer_id);
CREATE TABLE IF NOT EXISTS transfer_commands (
    id SERIAL PRIMARY KEY,
    transfer_id INTEGER NOT NULL REFERENCES transfers(id),
    warehouse VARCHAR(20) NOT NULL,
    step VARCHAR(20) NOT NULL,
    payload JSON NOT NULL,
    published_at TIMESTAMPTZ,
    UNIQUE (transfer_id, step)
);
CREATE INDEX IF NOT EXISTS ix_transfer_commands_transfer_id ON transfer_commands(transfer_id);
COMMIT;
