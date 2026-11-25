-- Migración de la central a usuarios, catálogo universal, solicitudes y notificaciones. Si la base es nueva no hace falta.
--   docker exec central_postgres psql -U postgres -d central_db -f /db/migrations/central_004_usuarios_catalogo.sql
BEGIN;

ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS nit VARCHAR(30) UNIQUE;
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS name VARCHAR(200);
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS location VARCHAR(120);
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS address VARCHAR(250);
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS capacity_units INTEGER;
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS admin_username VARCHAR(100);
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS heartbeat_secret VARCHAR(120);
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS registered_by VARCHAR(100);
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ;
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS catalog_sent_at TIMESTAMPTZ;

ALTER TABLE movement_history ADD COLUMN IF NOT EXISTS unit_price NUMERIC(14, 2);
ALTER TABLE movement_history ADD COLUMN IF NOT EXISTS counterparty VARCHAR(200);
ALTER TABLE movement_history ADD COLUMN IF NOT EXISTS created_by VARCHAR(100);

ALTER TABLE transfers ADD COLUMN IF NOT EXISTS requested_by VARCHAR(100);

ALTER TABLE transfer_commands ALTER COLUMN transfer_id DROP NOT NULL;
ALTER TABLE transfer_commands ALTER COLUMN command_type TYPE VARCHAR(40);

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
