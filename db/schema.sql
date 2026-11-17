-- Esquema de la base de datos de una bodega (Fases 1 y 2).
-- Docker lo ejecuta solo la primera vez que crea el volumen de PostgreSQL.

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
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_alerts_type CHECK (type IN ('OUT_OF_STOCK', 'DISCREPANCY'))
);

CREATE INDEX IF NOT EXISTS ix_alerts_created ON inventory_alerts (created_at);

COMMIT;
