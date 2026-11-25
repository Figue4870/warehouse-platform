BEGIN;

-- Bodegas conocidas y qué tan al día está la central con cada una
CREATE TABLE IF NOT EXISTS warehouses (
    warehouse_id           VARCHAR(20)  PRIMARY KEY,
    api_url                VARCHAR(200),
    first_seen_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_heartbeat_at      TIMESTAMPTZ,                      -- última respuesta VÁLIDA al heartbeat
    last_heartbeat_attempt_at TIMESTAMPTZ,                   -- última vez que la central preguntó
    last_heartbeat_error   TEXT,                             -- por qué falló la última pregunta
    heartbeat_failures     INTEGER      NOT NULL DEFAULT 0,  -- preguntas fallidas seguidas
    heartbeat_rejections   INTEGER      NOT NULL DEFAULT 0,  -- respuestas NO auténticas (posible suplantación)
    last_heartbeat_instance VARCHAR(60),                     -- qué copia de la API respondió
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
    last_sync_error        TEXT,
    -- registro hecho por el administrador global
    nit                    VARCHAR(30)  UNIQUE,
    name                   VARCHAR(200),
    location               VARCHAR(120),
    address                VARCHAR(250),
    description            TEXT,
    capacity_units         INTEGER,
    admin_username         VARCHAR(100),
    heartbeat_secret       VARCHAR(120),
    registered_by          VARCHAR(100),
    registered_at          TIMESTAMPTZ,
    catalog_sent_at        TIMESTAMPTZ                       -- cuándo recibió el catálogo universal completo
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
    unit_price          NUMERIC(14, 2),             -- precio real de la compra o la venta
    counterparty        VARCHAR(200),
    created_by          VARCHAR(100),
    lot                 VARCHAR(100),
    reference           VARCHAR(200),
    occurred_at         TIMESTAMPTZ  NOT NULL,
    projected_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    exported_at         TIMESTAMPTZ              -- cuándo se subió a S3 para Athena (NULL = pendiente)
);
CREATE INDEX IF NOT EXISTS ix_movement_history_pending ON movement_history (event_id) WHERE exported_at IS NULL;
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

-- Fase 6: Saga de transferencias (estado propio de la central)
CREATE TABLE IF NOT EXISTS transfers (
    id                        SERIAL        PRIMARY KEY,
    transfer_id               VARCHAR(30)   UNIQUE,          -- TR-000001
    sku                       VARCHAR(64)   NOT NULL,
    source_warehouse_id       VARCHAR(20)   NOT NULL,
    destination_warehouse_id  VARCHAR(20)   NOT NULL,
    quantity                  INTEGER       NOT NULL CHECK (quantity > 0),
    state                     VARCHAR(30)   NOT NULL,
    compensation_step         VARCHAR(20),
    failure_reason            TEXT,
    note                      TEXT,
    timeout_seconds           INTEGER       NOT NULL,
    reserve_deadline          TIMESTAMPTZ,
    receive_deadline          TIMESTAMPTZ,
    simulate_failure          VARCHAR(30),
    correlation_id            VARCHAR(80),
    requested_by              VARCHAR(100),
    created_at                TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ   NOT NULL DEFAULT now(),
    finished_at               TIMESTAMPTZ,
    exported_at               TIMESTAMPTZ,
    CONSTRAINT ck_transfers_state CHECK (state IN ('REQUESTED', 'RESERVING_SOURCE', 'SOURCE_RESERVED', 'IN_TRANSIT',
        'DESTINATION_RECEIVED', 'COMPLETED', 'FAILED', 'COMPENSATING', 'RESERVATION_RELEASED'))
);
CREATE INDEX IF NOT EXISTS ix_transfers_state ON transfers (state);

-- Cada cambio de estado
CREATE TABLE IF NOT EXISTS transfer_history (
    id            SERIAL        PRIMARY KEY,
    transfer_id   VARCHAR(30)   NOT NULL,
    from_state    VARCHAR(30),
    to_state      VARCHAR(30)   NOT NULL,
    event_id      VARCHAR(36)   UNIQUE,
    event_type    VARCHAR(60),
    warehouse_id  VARCHAR(20),
    detail        TEXT,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_transfer_history_transfer ON transfer_history (transfer_id);

-- OUTBOX de comandos de la central: pasos de la Saga y comandos de plataforma (los publica central_command_relay)
CREATE TABLE IF NOT EXISTS transfer_commands (
    id              SERIAL        PRIMARY KEY,
    command_id      VARCHAR(36)   NOT NULL UNIQUE,
    transfer_id     VARCHAR(30),                   -- NULL en los comandos de plataforma
    warehouse_id    VARCHAR(20)   NOT NULL,
    command_type    VARCHAR(40)   NOT NULL,
    payload         JSONB         NOT NULL,
    deadline        TIMESTAMPTZ,
    correlation_id  VARCHAR(80),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ,
    attempts        INTEGER       NOT NULL DEFAULT 0,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS ix_transfer_commands_transfer ON transfer_commands (transfer_id);
CREATE INDEX IF NOT EXISTS ix_transfer_commands_pending ON transfer_commands (id) WHERE published_at IS NULL;

-- Usuarios del directorio local y sus roles
CREATE TABLE IF NOT EXISTS users (
    username              VARCHAR(100) PRIMARY KEY,
    display_name          VARCHAR(200) NOT NULL,
    role                  VARCHAR(20)  NOT NULL,
    warehouse_id          VARCHAR(20),
    password_hash         VARCHAR(200),
    must_change_password  BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active             BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by            VARCHAR(100),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_login_at         TIMESTAMPTZ,
    CONSTRAINT ck_users_role CHECK (role IN ('ADMIN_GLOBAL', 'LIDER', 'ADMIN_BODEGA', 'REGISTRADOR')),
    CONSTRAINT ck_users_warehouse CHECK ((role IN ('ADMIN_BODEGA', 'REGISTRADOR')) = (warehouse_id IS NOT NULL))
);

-- Clave con la que la central firma los tokens (la comparten todas las instancias de la API)
CREATE TABLE IF NOT EXISTS signing_keys (
    kid          VARCHAR(40)  PRIMARY KEY,
    private_key  VARCHAR(100) NOT NULL,
    public_key   VARCHAR(100) NOT NULL,
    active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Catálogo universal (lo administra el administrador global y llega a todas las bodegas)
CREATE TABLE IF NOT EXISTS universal_products (
    sku              VARCHAR(64)  PRIMARY KEY,
    name             VARCHAR(200) NOT NULL,
    aliases          JSONB        NOT NULL DEFAULT '[]',
    description      TEXT,
    category         VARCHAR(100),
    unit             VARCHAR(30)  NOT NULL,
    supplier         VARCHAR(200),
    supplier_cost    NUMERIC(14, 2),
    retail_price     NUMERIC(14, 2),
    wholesale_price  NUMERIC(14, 2),
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    catalog_version  INTEGER      NOT NULL DEFAULT 1,
    created_by       VARCHAR(100),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Solicitudes de productos nuevos enviadas por las bodegas
CREATE TABLE IF NOT EXISTS product_requests (
    id                     SERIAL       PRIMARY KEY,
    warehouse_id           VARCHAR(20)  NOT NULL,
    request_id             INTEGER      NOT NULL,
    request_code           VARCHAR(40),
    status                 VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    product                JSONB        NOT NULL,
    requested_by           VARCHAR(100),
    approved_by_warehouse  VARCHAR(100),
    decided_by             VARCHAR(100),
    decided_at             TIMESTAMPTZ,
    decision_note          TEXT,
    sku                    VARCHAR(64),
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_product_requests_origin UNIQUE (warehouse_id, request_id),
    CONSTRAINT ck_product_requests_status CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED'))
);

-- Notificaciones del sistema por rol
CREATE TABLE IF NOT EXISTS notifications (
    id             SERIAL       PRIMARY KEY,
    audience_role  VARCHAR(20)  NOT NULL,
    warehouse_id   VARCHAR(20),
    severity       VARCHAR(10)  NOT NULL DEFAULT 'INFO',
    kind           VARCHAR(40)  NOT NULL,
    title          VARCHAR(200) NOT NULL,
    body           TEXT,
    dedupe_key     VARCHAR(120) UNIQUE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    read_at        TIMESTAMPTZ,
    read_by        VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS ix_notifications_audience ON notifications (audience_role, read_at);

COMMIT;
