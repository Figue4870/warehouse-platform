-- Migración de la central al heartbeat cifrado. Si la base es nueva no hace falta.
--   docker exec central_postgres psql -U postgres -d central_db -f /db/migrations/central_003_heartbeat_seguro.sql
BEGIN;

ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS last_heartbeat_attempt_at TIMESTAMPTZ;
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS last_heartbeat_error TEXT;
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS heartbeat_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS heartbeat_rejections INTEGER NOT NULL DEFAULT 0;
ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS last_heartbeat_instance VARCHAR(60);

COMMIT;
