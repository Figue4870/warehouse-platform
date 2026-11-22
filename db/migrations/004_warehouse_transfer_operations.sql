CREATE TABLE IF NOT EXISTS transfer_operations (
    transfer_id VARCHAR(24) NOT NULL,
    step VARCHAR(20) NOT NULL,
    result JSON NOT NULL,
    PRIMARY KEY (transfer_id, step)
);
