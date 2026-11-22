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
