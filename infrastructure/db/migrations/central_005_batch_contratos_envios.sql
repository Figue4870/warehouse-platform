-- Migración de la central a Batch, contratos con Lambda y envíos con Servientrega.
BEGIN;

ALTER TABLE transfers DROP CONSTRAINT IF EXISTS ck_transfers_state;
ALTER TABLE transfers ADD CONSTRAINT ck_transfers_state CHECK (state IN ('REQUESTED', 'RESERVING_SOURCE', 'SOURCE_RESERVED',
    'SHIPPING', 'IN_TRANSIT', 'DESTINATION_RECEIVED', 'COMPLETED', 'FAILED', 'COMPENSATING', 'RESERVATION_RELEASED'));

-- Fase 8: ejecuciones del proceso Batch (programadas, manuales y de recuperación)
CREATE TABLE IF NOT EXISTS batch_runs (
    id                    SERIAL       PRIMARY KEY,
    job_name              VARCHAR(60)  NOT NULL,
    business_date         DATE         NOT NULL,
    status                VARCHAR(20)  NOT NULL,
    trigger               VARCHAR(20)  NOT NULL,
    requested_by          VARCHAR(100),
    attempt               INTEGER      NOT NULL DEFAULT 1,
    warehouses_processed  INTEGER,
    movements_processed   INTEGER,
    stale_warehouses      JSONB,
    outputs               JSONB,
    error                 TEXT,
    correlation_id        VARCHAR(80),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at            TIMESTAMPTZ,
    finished_at           TIMESTAMPTZ,
    CONSTRAINT ck_batch_runs_status CHECK (status IN ('REQUESTED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')),
    CONSTRAINT ck_batch_runs_trigger CHECK (trigger IN ('SCHEDULE', 'MANUAL', 'CATCH_UP'))
);
CREATE INDEX IF NOT EXISTS ix_batch_runs_job_date ON batch_runs (job_name, business_date);

-- Fase 9: contratos de proveedores (documento en S3; metadata extraída por la Lambda)
CREATE TABLE IF NOT EXISTS contracts (
    contract_id        VARCHAR(20)   PRIMARY KEY,
    title              VARCHAR(200)  NOT NULL,
    supplier_nit       VARCHAR(30),
    supplier_name      VARCHAR(200),
    supplier_verified  BOOLEAN,
    signed_on          DATE,
    valid_until        DATE,
    agreed_prices      JSONB         NOT NULL DEFAULT '[]',
    metadata_source    VARCHAR(20),
    extracted          JSONB,
    document_key       VARCHAR(300)  NOT NULL,
    document_name      VARCHAR(200),
    size_bytes         INTEGER,
    status             VARCHAR(20)   NOT NULL DEFAULT 'UPLOADED',
    sha256             VARCHAR(64),
    pages              INTEGER,
    validation_errors  JSONB,
    uploaded_by        VARCHAR(100),
    uploaded_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    processed_at       TIMESTAMPTZ,
    reviewed_by        VARCHAR(100),
    reviewed_at        TIMESTAMPTZ,
    review_note        TEXT,
    CONSTRAINT ck_contracts_status CHECK (status IN ('UPLOADED', 'EXTRACTED', 'INVALID', 'APPROVED', 'REJECTED', 'TERMINATED')),
    CONSTRAINT ck_contracts_dates CHECK (valid_until IS NULL OR signed_on IS NULL OR valid_until >= signed_on)
);
CREATE INDEX IF NOT EXISTS ix_contracts_supplier ON contracts (supplier_nit);

-- Avisos de la Lambda (uno por objeto y versión)
CREATE TABLE IF NOT EXISTS storage_events (
    event_id     VARCHAR(120)  PRIMARY KEY,
    kind         VARCHAR(40)   NOT NULL,
    object_key   VARCHAR(300)  NOT NULL,
    outcome      VARCHAR(20)   NOT NULL,
    detail       JSONB,
    received_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_storage_events_key ON storage_events (object_key);

-- Envíos con Servientrega dentro de la Saga
CREATE TABLE IF NOT EXISTS shipments (
    id               SERIAL       PRIMARY KEY,
    transfer_id      VARCHAR(30)  NOT NULL UNIQUE,
    status           VARCHAR(30)  NOT NULL DEFAULT 'PENDING',
    tracking_number  VARCHAR(40),
    attempts         INTEGER      NOT NULL DEFAULT 0,
    last_error       TEXT,
    next_attempt_at  TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_shipments_status CHECK (status IN ('PENDING', 'CREATED', 'IN_TRANSIT', 'DELIVERED', 'RETURNED', 'ERROR'))
);
CREATE INDEX IF NOT EXISTS ix_shipments_pending ON shipments (next_attempt_at) WHERE status = 'PENDING';

COMMIT;
