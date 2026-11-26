"""Workers de la extensión IoT: almacenamiento, alertas y analítica. Los tres leen la misma lectura (cada uno su cola).

  python -m iot_service.workers storage | alerts | analytics
"""

import hashlib
import hmac
import json
import logging
import sys
import time
from datetime import datetime, timezone

import httpx
from platform_common.messaging import Consumer, PermanentError
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from iot_service import models
from iot_service.config import settings
from iot_service.database import SessionLocal
from iot_service.logs import setup_logging
from iot_service.readings import EXCHANGES, Reading, declare_ingestion_topology, queues
from iot_service.rules import evaluate

logger = logging.getLogger("iot.workers")


def parse(body: bytes) -> Reading:
    try:
        return Reading.model_validate_json(body)
    except ValidationError as error:
        raise PermanentError(f"Lectura inválida: {error.errors()[0]['msg']}") from error


def _locked(db: Session, model, key):
    query = db.query(model).filter(model.__mapper__.primary_key[0] == key)
    return query.with_for_update().first() if db.bind.dialect.name == "postgresql" else query.first()


# almacenamiento
def store(db: Session, reading: Reading) -> str:
    """Guarda la lectura una sola vez y avanza el último estado del sensor solo con secuencias nuevas."""
    if db.get(models.ReadingEntity, reading.event_id) is not None:
        return "duplicate"
    state = _locked(db, models.SensorStateEntity, reading.sensor_id)
    late = state is not None and reading.sequence_number < state.last_sequence
    db.add(models.ReadingEntity(
        event_id=reading.event_id, warehouse_id=reading.warehouse_id, sensor_id=reading.sensor_id,
        sensor_type=reading.sensor_type, sequence_number=reading.sequence_number, value=reading.value, unit=reading.unit,
        measured_at=reading.timestamp, gateway_received_at=reading.gateway_received_at, late=int(late or reading.late)))
    if state is None:
        db.add(models.SensorStateEntity(sensor_id=reading.sensor_id, warehouse_id=reading.warehouse_id,
                                        sensor_type=reading.sensor_type, last_sequence=reading.sequence_number,
                                        last_value=reading.value, unit=reading.unit, last_measured_at=reading.timestamp,
                                        readings=1, late_readings=0, gaps=0))
    elif late:
        state.readings += 1
        state.late_readings += 1
        state.gaps = max(state.gaps - 1, 0)  # el hueco que dejó esta lectura ya no existe
    else:
        state.gaps += max(reading.sequence_number - state.last_sequence - 1, 0)
        state.last_sequence, state.last_value, state.last_measured_at = reading.sequence_number, reading.value, reading.timestamp
        state.readings += 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # otra instancia la guardó al mismo tiempo
        return "duplicate"
    return "late" if late else "stored"


# alertas
def sign(secret: str, body: bytes) -> dict:
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Signature-Timestamp": timestamp, "X-Signature": signature}


def notify_central(alert: models.IotAlertEntity) -> bool:
    if not settings.IOT_SHARED_SECRET or not settings.CENTRAL_API_URL:
        return False
    body = json.dumps({"alert_id": alert.id, "warehouse_id": alert.warehouse_id, "sensor_id": alert.sensor_id,
                       "sensor_type": alert.sensor_type, "rule": alert.rule, "severity": alert.severity,
                       "message": alert.message, "opened_at": alert.opened_at.isoformat()}).encode()
    try:
        response = httpx.post(f"{settings.CENTRAL_API_URL.rstrip('/')}/integrations/iot-alerts", content=body,
                              headers=sign(settings.IOT_SHARED_SECRET, body), timeout=3)
        return response.status_code < 300
    except httpx.HTTPError:
        return False


def notify_pending(db: Session, notifier=notify_central, limit: int = 10) -> int:
    """Alertas que no alcanzaron a llegar a la central (caída): se reintentan con cada lectura nueva."""
    sent = 0
    for alert in (db.query(models.IotAlertEntity).filter(models.IotAlertEntity.notified_at.is_(None))
                  .order_by(models.IotAlertEntity.id).limit(limit).all()):
        if notifier(alert):
            alert.notified_at = datetime.now(timezone.utc)
            sent += 1
    db.commit()
    return sent


def check_alerts(db: Session, reading: Reading, notifier=notify_central) -> str:
    row = _locked(db, models.RuleStateEntity, reading.sensor_id)
    if row is not None and reading.sequence_number <= row.last_sequence:
        return "skipped"  # repetida o atrasada: las reglas siguen el orden de las secuencias
    rule, abnormal, detail, state = evaluate(reading.sensor_type, reading.value, reading.timestamp, row.state if row else {})
    if row is None:
        row = models.RuleStateEntity(sensor_id=reading.sensor_id, last_sequence=reading.sequence_number)
        db.add(row)
    row.last_sequence, row.state, row.updated_at = reading.sequence_number, state, datetime.now(timezone.utc)
    result = "normal"
    if rule is not None:
        open_alert = (db.query(models.IotAlertEntity).filter(models.IotAlertEntity.sensor_id == reading.sensor_id,
                                                             models.IotAlertEntity.rule == rule.name,
                                                             models.IotAlertEntity.closed_at.is_(None)).first())
        if abnormal and open_alert is None and state["streak"] >= rule.confirm:
            alert = models.IotAlertEntity(warehouse_id=reading.warehouse_id, sensor_id=reading.sensor_id,
                                          sensor_type=reading.sensor_type, rule=rule.name, severity=rule.severity,
                                          message=f"{rule.message}: {reading.sensor_id} {detail}", value=reading.value,
                                          opened_at=reading.timestamp)
            db.add(alert)
            result = "opened"
            logger.warning("Alerta de sensor", extra={"event": "iot_alert_opened", "context": {
                "warehouse_id": reading.warehouse_id, "sensor_id": reading.sensor_id, "rule": rule.name, "detail": detail}})
        elif not abnormal and open_alert is not None:
            open_alert.closed_at = reading.timestamp
            result = "closed"
    db.commit()
    notify_pending(db, notifier)
    return result


# analítica
def aggregate(db: Session, reading: Reading, now: datetime | None = None) -> str:
    """Suma la lectura al agregado de su minuto. La tabla de vistas hace que una repetición no cuente dos veces."""
    now = now or datetime.now(timezone.utc)
    db.add(models.AnalyticsSeenEntity(event_id=reading.event_id))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return "duplicate"
    minute = reading.timestamp.astimezone(timezone.utc).replace(second=0, microsecond=0)
    latency_ms = max((now - reading.timestamp).total_seconds() * 1000, 0.0)
    query = db.query(models.MinuteStatEntity).filter_by(warehouse_id=reading.warehouse_id, sensor_type=reading.sensor_type,
                                                        minute=minute)
    stat = (query.with_for_update() if db.bind.dialect.name == "postgresql" else query).first()
    if stat is None:
        stat = models.MinuteStatEntity(warehouse_id=reading.warehouse_id, sensor_type=reading.sensor_type, minute=minute,
                                       readings=0, value_sum=0.0, latency_ms_sum=0.0)
        db.add(stat)
    stat.readings += 1
    stat.value_sum += reading.value
    stat.value_min = reading.value if stat.value_min is None else min(stat.value_min, reading.value)
    stat.value_max = reading.value if stat.value_max is None else max(stat.value_max, reading.value)
    stat.latency_ms_sum += latency_ms
    stat.latency_ms_max = latency_ms if stat.latency_ms_max is None else max(stat.latency_ms_max, latency_ms)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # otro worker creó el mismo minuto: se reintenta el mensaje
        raise
    return "aggregated"


HANDLERS = {"storage": store, "alerts": check_alerts, "analytics": aggregate}


def make_handler(name: str, session_factory=SessionLocal):
    action = HANDLERS[name]

    def handle(body, properties, routing_key):
        reading = parse(body)
        with session_factory() as db:
            action(db, reading)

    return handle


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "storage"
    setup_logging(f"iot-{name}")
    spec = queues(settings.WORKER_RETRY_DELAY_MS, settings.WORKER_MAX_RETRIES)[name]
    Consumer(settings.AMQP_URL, f"iot-{name}-{settings.INSTANCE_NAME}", spec, EXCHANGES, make_handler(name),
             declare_topology=lambda ch: declare_ingestion_topology(ch, settings.WORKER_RETRY_DELAY_MS, settings.WORKER_MAX_RETRIES),
             prefetch=50).run()


if __name__ == "__main__":
    main()
