ALTER TABLE movement_history ADD COLUMN IF NOT EXISTS exported_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_movement_history_pending_export
    ON movement_history (occurred_at, event_id) WHERE exported_at IS NULL;
