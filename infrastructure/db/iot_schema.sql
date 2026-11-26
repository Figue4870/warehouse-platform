-- Fase 12: base de la extensión IoT (lecturas, estado de cada sensor, alertas y agregados por minuto).
BEGIN;

CREATE TABLE IF NOT EXISTS sensor_readings (
    event_id             VARCHAR(80)  PRIMARY KEY,           -- idempotencia: una lectura se guarda una vez
    warehouse_id         VARCHAR(20)  NOT NULL,
    sensor_id            VARCHAR(40)  NOT NULL,
    sensor_type          VARCHAR(20)  NOT NULL,
    sequence_number      BIGINT       NOT NULL,
    value                DOUBLE PRECISION NOT NULL,
    unit                 VARCHAR(10),
    measured_at          TIMESTAMPTZ  NOT NULL,              -- reloj del sensor
    gateway_received_at  TIMESTAMPTZ,
    stored_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    late                 INTEGER      NOT NULL DEFAULT 0,
    CONSTRAINT ck_sensor_type CHECK (sensor_type IN ('temperature', 'humidity', 'motion', 'door', 'energy'))
);
CREATE INDEX IF NOT EXISTS ix_readings_sensor_time ON sensor_readings (warehouse_id, sensor_id, measured_at);
CREATE INDEX IF NOT EXISTS ix_readings_stored ON sensor_readings (stored_at);

CREATE TABLE IF NOT EXISTS sensor_state (
    sensor_id         VARCHAR(40)  PRIMARY KEY,
    warehouse_id      VARCHAR(20)  NOT NULL,
    sensor_type       VARCHAR(20)  NOT NULL,
    last_sequence     BIGINT       NOT NULL,
    last_value        DOUBLE PRECISION NOT NULL,
    unit              VARCHAR(10),
    last_measured_at  TIMESTAMPTZ  NOT NULL,
    readings          BIGINT       NOT NULL DEFAULT 0,
    late_readings     BIGINT       NOT NULL DEFAULT 0,
    gaps              BIGINT       NOT NULL DEFAULT 0,
    rule_state        JSONB
);

CREATE TABLE IF NOT EXISTS iot_alerts (
    id            SERIAL       PRIMARY KEY,
    warehouse_id  VARCHAR(20)  NOT NULL,
    sensor_id     VARCHAR(40)  NOT NULL,
    sensor_type   VARCHAR(20)  NOT NULL,
    rule          VARCHAR(40)  NOT NULL,
    severity      VARCHAR(10)  NOT NULL,
    message       TEXT         NOT NULL,
    value         DOUBLE PRECISION,
    opened_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    closed_at     TIMESTAMPTZ,
    notified_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_iot_alerts_open ON iot_alerts (sensor_id, rule, closed_at);
-- Una sola alerta abierta por sensor y regla
CREATE UNIQUE INDEX IF NOT EXISTS uq_iot_alerts_one_open ON iot_alerts (sensor_id, rule) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS iot_rule_state (
    sensor_id      VARCHAR(40)  PRIMARY KEY,
    last_sequence  BIGINT       NOT NULL,
    state          JSONB,
    updated_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS iot_minute_stats (
    id              SERIAL       PRIMARY KEY,
    warehouse_id    VARCHAR(20)  NOT NULL,
    sensor_type     VARCHAR(20)  NOT NULL,
    minute          TIMESTAMPTZ  NOT NULL,
    readings        INTEGER      NOT NULL DEFAULT 0,
    value_sum       DOUBLE PRECISION NOT NULL DEFAULT 0,
    value_min       DOUBLE PRECISION,
    value_max       DOUBLE PRECISION,
    latency_ms_sum  DOUBLE PRECISION NOT NULL DEFAULT 0,
    latency_ms_max  DOUBLE PRECISION,
    CONSTRAINT uq_minute_stats UNIQUE (warehouse_id, sensor_type, minute)
);

CREATE TABLE IF NOT EXISTS iot_analytics_seen (
    event_id  VARCHAR(80)  PRIMARY KEY,
    seen_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMIT;
