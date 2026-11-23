BEGIN;

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
