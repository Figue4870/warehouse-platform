-- Esquema de la base de datos de una bodega (Fases 1 a 3 y 6).
-- Docker lo ejecuta solo la primera vez; para bases existentes usar db/migrations/.

BEGIN;

-- Productos (catálogo)
CREATE TABLE IF NOT EXISTS products (
    id               SERIAL PRIMARY KEY,
    sku              VARCHAR(64)  NOT NULL,              -- código universal
    name             VARCHAR(200) NOT NULL,              -- nombre universal
    description      TEXT,
    category         VARCHAR(100),
    unit             VARCHAR(30)  NOT NULL,
    supplier         VARCHAR(200),                       -- proveedor asociado
    lot              VARCHAR(100),                       -- lote
    supplier_cost    NUMERIC(14, 2),                     -- costo del proveedor
    retail_price     NUMERIC(14, 2),                     -- precio venta en tienda
    wholesale_price  NUMERIC(14, 2),                     -- precio mayoristas/terceros
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE, -- FALSE = dado de baja
    version          INTEGER      NOT NULL DEFAULT 1,    -- Fase 3: sube con cada cambio
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_products_sku             UNIQUE (sku),
    CONSTRAINT ck_products_supplier_cost   CHECK (supplier_cost >= 0),
    CONSTRAINT ck_products_retail_price    CHECK (retail_price >= 0),
    CONSTRAINT ck_products_wholesale_price CHECK (wholesale_price >= 0)
);

-- Saldo actual por producto. Los CHECK impiden saldos negativos.
CREATE TABLE IF NOT EXISTS inventory (
    id                 SERIAL PRIMARY KEY,
    product_id         INTEGER     NOT NULL REFERENCES products (id),
    warehouse_id       VARCHAR(20) NOT NULL,
    quantity           INTEGER     NOT NULL DEFAULT 0,
    reserved_quantity  INTEGER     NOT NULL DEFAULT 0,
    version            INTEGER     NOT NULL DEFAULT 0,   -- Fase 3: sube con cada movimiento
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_inventory_product_warehouse     UNIQUE (product_id, warehouse_id),
    CONSTRAINT ck_inventory_quantity_non_negative CHECK (quantity >= 0),
    CONSTRAINT ck_inventory_reserved_non_negative CHECK (reserved_quantity >= 0)
);

-- Historial de movimientos (kardex)
CREATE TABLE IF NOT EXISTS inventory_movements (
    id              SERIAL PRIMARY KEY,
    product_id      INTEGER      NOT NULL REFERENCES products (id),
    warehouse_id    VARCHAR(20)  NOT NULL,
    type            VARCHAR(20)  NOT NULL,
    quantity        INTEGER      NOT NULL,   -- en ADJUSTMENT puede ser negativo
    quantity_after  INTEGER      NOT NULL,   -- disponible después del movimiento
    reserved_after  INTEGER      NOT NULL,   -- reservado después del movimiento
    lot             VARCHAR(100),
    reference       VARCHAR(200),            -- factura, guía, motivo...
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_movements_type CHECK (type IN (
        'RECEIPT', 'SHIPMENT', 'ADJUSTMENT', 'RESERVATION',
        'RELEASE', 'TRANSFER_OUT', 'TRANSFER_IN', 'RETURN')),
    CONSTRAINT ck_movements_quantity_not_zero CHECK (quantity <> 0)
);

CREATE INDEX IF NOT EXISTS ix_movements_product_created
    ON inventory_movements (product_id, created_at);

-- Alertas automáticas: producto agotado y desfases
CREATE TABLE IF NOT EXISTS inventory_alerts (
    id            SERIAL PRIMARY KEY,
    product_id    INTEGER     NOT NULL REFERENCES products (id),
    warehouse_id  VARCHAR(20) NOT NULL,
    type          VARCHAR(20) NOT NULL,
    message       TEXT        NOT NULL,
    movement_id   INTEGER     REFERENCES inventory_movements (id),
    notified_at   TIMESTAMPTZ,                 -- Fase 3: cuándo se avisó al administrador
    notification_channel VARCHAR(40),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_alerts_type CHECK (type IN ('OUT_OF_STOCK', 'DISCREPANCY'))
);

CREATE INDEX IF NOT EXISTS ix_alerts_created ON inventory_alerts (created_at);

-- Fase 3: outbox de eventos y reportes asíncronos

-- Outbox: el evento se guarda en la misma transacción que el cambio y el relay lo publica.
CREATE TABLE IF NOT EXISTS outbox_events (
    id              BIGSERIAL    PRIMARY KEY,
    event_id        VARCHAR(36)  NOT NULL,
    event_type      VARCHAR(60)  NOT NULL,
    event_version   SMALLINT     NOT NULL DEFAULT 1,
    routing_key     VARCHAR(120) NOT NULL,
    aggregate_type  VARCHAR(40)  NOT NULL,
    aggregate_id    VARCHAR(80)  NOT NULL,
    payload         JSONB        NOT NULL,
    correlation_id  VARCHAR(80),
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    sequence        BIGINT,                 -- 1, 2, 3... sin huecos (lo asigna el relay)
    published_at    TIMESTAMPTZ,            -- NULL = pendiente
    attempts        INTEGER      NOT NULL DEFAULT 0,
    last_error      TEXT,
    CONSTRAINT uq_outbox_event_id UNIQUE (event_id),
    CONSTRAINT uq_outbox_sequence UNIQUE (sequence)
);

-- Índice parcial: encontrar pendientes es instantáneo aunque haya millones publicados
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox_events (id) WHERE published_at IS NULL;

-- Reportes pedidos con POST /reports (el resultado se guarda aquí: la API sigue stateless)
CREATE TABLE IF NOT EXISTS report_jobs (
    id              VARCHAR(40)  PRIMARY KEY,
    warehouse_id    VARCHAR(20)  NOT NULL,
    report_type     VARCHAR(40)  NOT NULL,
    params          JSONB        NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    result          JSONB,
    error           TEXT,
    attempts        INTEGER      NOT NULL DEFAULT 0,
    correlation_id  VARCHAR(80),
    requested_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    CONSTRAINT ck_report_jobs_status CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'))
);

-- Fase 6: lo que esta bodega hizo en cada paso de una transferencia (Saga).
-- (transfer_id, step) único = un mismo paso nunca se ejecuta dos veces.
CREATE TABLE IF NOT EXISTS transfer_operations (
    id              SERIAL       PRIMARY KEY,
    transfer_id     VARCHAR(30)  NOT NULL,
    step            VARCHAR(20)  NOT NULL,
    outcome         VARCHAR(20)  NOT NULL,
    sku             VARCHAR(64),
    product_id      INTEGER      REFERENCES products (id),
    quantity        INTEGER,
    reason          TEXT,
    movement_id     INTEGER      REFERENCES inventory_movements (id),
    command_id      VARCHAR(64),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_transfer_operations_step UNIQUE (transfer_id, step),
    CONSTRAINT ck_transfer_operations_step CHECK (step IN ('RESERVE', 'RECEIVE', 'COMMIT', 'RELEASE', 'CANCEL_RECEIVE')),
    CONSTRAINT ck_transfer_operations_outcome CHECK (outcome IN ('DONE', 'REJECTED', 'NOOP'))
);
CREATE INDEX IF NOT EXISTS ix_transfer_operations_transfer ON transfer_operations (transfer_id);

COMMIT;
