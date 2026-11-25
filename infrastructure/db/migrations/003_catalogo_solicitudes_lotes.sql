-- Migración de una bodega a usuarios, catálogo universal, solicitudes y lotes. Si la base es nueva no hace falta.
--   docker exec db_warehouse_WH-001 psql -U postgres -d warehouse_db -f /db/migrations/003_catalogo_solicitudes_lotes.sql
BEGIN;

ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS unit_price NUMERIC(14, 2);
ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS counterparty VARCHAR(200);
ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS created_by VARCHAR(100);

ALTER TABLE inventory_alerts ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ;
ALTER TABLE inventory_alerts ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(100);
ALTER TABLE inventory_alerts DROP CONSTRAINT IF EXISTS ck_alerts_type;
ALTER TABLE inventory_alerts ADD CONSTRAINT ck_alerts_type CHECK (type IN ('OUT_OF_STOCK', 'DISCREPANCY', 'INCONSISTENCY'));

-- Unidades por lote (verificación de lotes en entradas y salidas)
CREATE TABLE IF NOT EXISTS inventory_lots (
    id            SERIAL       PRIMARY KEY,
    product_id    INTEGER      NOT NULL REFERENCES products (id),
    warehouse_id  VARCHAR(20)  NOT NULL,
    lot           VARCHAR(100) NOT NULL,
    quantity      INTEGER      NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_inventory_lots UNIQUE (product_id, warehouse_id, lot),
    CONSTRAINT ck_inventory_lots_non_negative CHECK (quantity >= 0)
);

-- Copia local del catálogo universal (la central la mantiene con comandos CATALOG_UPSERT)
CREATE TABLE IF NOT EXISTS universal_products (
    sku              VARCHAR(64)  PRIMARY KEY,
    name             VARCHAR(200) NOT NULL,
    aliases          TEXT,
    description      TEXT,
    category         VARCHAR(100),
    unit             VARCHAR(30)  NOT NULL,
    supplier         VARCHAR(200),
    supplier_cost    NUMERIC(14, 2),
    retail_price     NUMERIC(14, 2),
    wholesale_price  NUMERIC(14, 2),
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    catalog_version  INTEGER      NOT NULL DEFAULT 1,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Solicitudes de los registradores: producto nuevo o ajuste por desfase
CREATE TABLE IF NOT EXISTS operation_requests (
    id             SERIAL       PRIMARY KEY,
    warehouse_id   VARCHAR(20)  NOT NULL,
    type           VARCHAR(20)  NOT NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    product_id     INTEGER      REFERENCES products (id),
    details        JSONB        NOT NULL,
    created_by     VARCHAR(100) NOT NULL,
    decided_by     VARCHAR(100),
    decided_at     TIMESTAMPTZ,
    decision_note  TEXT,
    alert_id       INTEGER      REFERENCES inventory_alerts (id),
    movement_id    INTEGER      REFERENCES inventory_movements (id),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_requests_type CHECK (type IN ('NEW_PRODUCT', 'ADJUSTMENT')),
    CONSTRAINT ck_requests_status CHECK (status IN ('PENDING', 'FORWARDED', 'APPROVED', 'REJECTED'))
);
CREATE INDEX IF NOT EXISTS ix_requests_status ON operation_requests (status, created_at);

-- Claves públicas de la central para verificar tokens sin depender de ella
CREATE TABLE IF NOT EXISTS trusted_keys (
    kid         VARCHAR(40) PRIMARY KEY,
    public_key  TEXT        NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
