"""Datos de la extensión IoT: lecturas, último estado de cada sensor, alertas y agregados por minuto."""

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint

from iot_service.database import Base


def _now():
    return datetime.now(timezone.utc)


class ReadingEntity(Base):
    """Cada lectura una sola vez (event_id), aunque el sensor o el gateway la repitan."""

    __tablename__ = "sensor_readings"
    __table_args__ = (Index("ix_readings_sensor_time", "warehouse_id", "sensor_id", "measured_at"),)

    event_id = Column(String(80), primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    sensor_id = Column(String(40), nullable=False)
    sensor_type = Column(String(20), nullable=False)
    sequence_number = Column(BigInteger, nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(10))
    measured_at = Column(DateTime(timezone=True), nullable=False)      # reloj del sensor
    gateway_received_at = Column(DateTime(timezone=True))
    stored_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    late = Column(Integer, nullable=False, default=0)                  # 1 = llegó después de una más nueva


class SensorStateEntity(Base):
    """Último valor conocido por sensor. Solo avanza con una secuencia mayor (orden por sequence_number)."""

    __tablename__ = "sensor_state"

    sensor_id = Column(String(40), primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    sensor_type = Column(String(20), nullable=False)
    last_sequence = Column(BigInteger, nullable=False)
    last_value = Column(Float, nullable=False)
    unit = Column(String(10))
    last_measured_at = Column(DateTime(timezone=True), nullable=False)
    readings = Column(BigInteger, nullable=False, default=0)
    late_readings = Column(BigInteger, nullable=False, default=0)
    gaps = Column(BigInteger, nullable=False, default=0)               # secuencias que nunca llegaron (por ahora)
    rule_state = Column(JSON)                                          # estado de las reglas de alerta


class IotAlertEntity(Base):
    __tablename__ = "iot_alerts"
    __table_args__ = (Index("ix_iot_alerts_open", "sensor_id", "rule", "closed_at"),)

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    sensor_id = Column(String(40), nullable=False)
    sensor_type = Column(String(20), nullable=False)
    rule = Column(String(40), nullable=False)
    severity = Column(String(10), nullable=False)
    message = Column(Text, nullable=False)
    value = Column(Float)
    opened_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    closed_at = Column(DateTime(timezone=True))
    notified_at = Column(DateTime(timezone=True))


class MinuteStatEntity(Base):
    """Agregados por bodega, tipo de sensor y minuto (los consume el tablero y la analítica)."""

    __tablename__ = "iot_minute_stats"
    __table_args__ = (UniqueConstraint("warehouse_id", "sensor_type", "minute", name="uq_minute_stats"),)

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    sensor_type = Column(String(20), nullable=False)
    minute = Column(DateTime(timezone=True), nullable=False)
    readings = Column(Integer, nullable=False, default=0)
    value_sum = Column(Float, nullable=False, default=0.0)
    value_min = Column(Float)
    value_max = Column(Float)
    latency_ms_sum = Column(Float, nullable=False, default=0.0)
    latency_ms_max = Column(Float)


class AnalyticsSeenEntity(Base):
    """Lecturas ya sumadas a los agregados (para que una repetición no cuente dos veces)."""

    __tablename__ = "iot_analytics_seen"

    event_id = Column(String(80), primary_key=True)
    seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class RuleStateEntity(Base):
    """Estado de las reglas de alerta de cada sensor (lo usa solo el worker de alertas)."""

    __tablename__ = "iot_rule_state"

    sensor_id = Column(String(40), primary_key=True)
    last_sequence = Column(BigInteger, nullable=False)
    state = Column(JSON)
    updated_at = Column(DateTime(timezone=True))
