"""Servicio de ingesta IoT y consultas.

POST /ingest recibe lotes del gateway, valida cada lectura y la publica en RabbitMQ (iot.events) para los
workers de almacenamiento, alertas y analítica. Responde 202: el procesamiento es asíncrono.
"""

import logging
import statistics
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from platform_common.messaging import ReliablePublisher
from platform_common.metrics import Counter, Registry, gauge, instrument_fastapi, instrument_sqlalchemy, postgres_collector, rabbitmq_collector
from pydantic import ValidationError
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from iot_service import models
from iot_service.config import settings
from iot_service.database import Base, SessionLocal, engine, get_db
from iot_service.logs import setup_logging
from iot_service.readings import EXCHANGES, Reading, declare_ingestion_topology, routing_key

logger = logging.getLogger("iot.api")
registry = Registry("iot-api")
ingested = Counter("iot_readings_ingested_total", "Lecturas aceptadas por la ingesta")
rejected = Counter("iot_readings_rejected_total", "Lecturas rechazadas por la ingesta")
ingested.inc(0)   # en 0 desde el arranque: rate() tiene serie aunque aún no llegue ninguna lectura
rejected.inc(0)
_publisher: Optional[ReliablePublisher] = None
_lock = threading.Lock()


def publisher() -> ReliablePublisher:
    global _publisher
    if _publisher is None:
        _publisher = ReliablePublisher(settings.AMQP_URL, f"iot-ingestion-{settings.INSTANCE_NAME}",
                                       on_connect=lambda ch: declare_ingestion_topology(ch, settings.WORKER_RETRY_DELAY_MS,
                                                                                        settings.WORKER_MAX_RETRIES))
    return _publisher


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("iot-api")
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Warehouse Cloud Platform — IoT", version="1.0.0", lifespan=lifespan,
              description="Fase 12: ingesta de lecturas de sensores (gateway -> RabbitMQ -> almacenamiento, alertas y analítica).")
instrument_fastapi(app, registry)
instrument_sqlalchemy(engine, registry)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "instance": settings.INSTANCE_NAME}


@app.post("/ingest", status_code=status.HTTP_202_ACCEPTED, summary="Lote de lecturas del gateway")
def ingest(batch: dict, x_gateway_key: Optional[str] = Header(None)):
    if settings.GATEWAY_KEY and x_gateway_key != settings.GATEWAY_KEY:
        raise HTTPException(status_code=401, detail="Gateway no autorizado")
    readings, errors = [], []
    for raw in batch.get("readings") or []:
        try:
            readings.append(Reading.model_validate(raw))
        except ValidationError as error:
            errors.append({"event_id": raw.get("event_id") if isinstance(raw, dict) else None, "error": error.errors()[0]["msg"]})
    try:
        with _lock:  # una conexión AMQP por proceso: no es segura entre hilos
            for reading in readings:
                publisher().publish(exchange=EXCHANGES.events, routing_key=routing_key(reading),
                                    body=reading.model_dump_json().encode(), message_id=reading.event_id,
                                    message_type="IOT_READING")
    except Exception as error:  # noqa: BLE001 - RabbitMQ caído: el gateway conserva el lote y lo reintenta
        logger.warning("No se pudo publicar el lote", extra={"event": "ingest_failed", "context": {"error": str(error)[:200]}})
        raise HTTPException(status_code=503, detail="Cola de ingesta no disponible") from error
    ingested.inc(len(readings), gateway=str(batch.get("gateway_id", "?")))
    if errors:
        rejected.inc(len(errors), gateway=str(batch.get("gateway_id", "?")))
    return {"accepted": len(readings), "rejected": len(errors), "errors": errors[:10]}


def _pct(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))], 1)


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@app.get("/iot/summary", summary="Estado por bodega: sensores, últimos valores, alertas abiertas, rendimiento y latencia")
def summary(minutes: int = Query(5, ge=1, le=1440), db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    states = db.query(models.SensorStateEntity).all()
    open_alerts = db.query(models.IotAlertEntity).filter(models.IotAlertEntity.closed_at.is_(None)).all()
    recent = (db.query(models.ReadingEntity.warehouse_id, models.ReadingEntity.measured_at, models.ReadingEntity.stored_at)
              .filter(models.ReadingEntity.stored_at >= since).all())
    by_warehouse: dict[str, dict] = {}
    for s in states:
        w = by_warehouse.setdefault(s.warehouse_id, {"warehouse_id": s.warehouse_id, "sensors": 0, "by_type": {}, "open_alerts": 0,
                                                     "late_readings": 0, "gaps": 0, "readings": 0})
        w["sensors"] += 1
        w["late_readings"] += s.late_readings
        w["gaps"] += s.gaps
        w["readings"] += s.readings
        t = w["by_type"].setdefault(s.sensor_type, {"sensors": 0, "values": []})
        t["sensors"] += 1
        t["values"].append(s.last_value)
    for w in by_warehouse.values():
        for t in w["by_type"].values():
            values = t.pop("values")
            t["avg"] = round(statistics.fmean(values), 2) if values else None
            t["max"] = max(values) if values else None
    for alert in open_alerts:
        by_warehouse.setdefault(alert.warehouse_id, {"warehouse_id": alert.warehouse_id, "sensors": 0, "by_type": {},
                                                     "open_alerts": 0})["open_alerts"] += 1
    latencies = [(stored - measured).total_seconds() * 1000 for _, measured, stored in recent if stored and measured]
    last_minute = datetime.now(timezone.utc) - timedelta(seconds=60)
    return {
        "window_minutes": minutes,
        "readings_in_window": len(recent),
        # ritmo actual: el último minuto; promediar toda la ventana lo subestima cuando los sensores acaban de empezar
        "throughput_per_second": round(sum(1 for _, _, stored in recent if stored and _utc(stored) >= last_minute) / 60, 2),
        "latency_ms": {"p50": _pct(latencies, 50), "p95": _pct(latencies, 95), "p99": _pct(latencies, 99)},
        "warehouses": sorted(by_warehouse.values(), key=lambda w: w["warehouse_id"]),
        "open_alerts": len(open_alerts),
    }


@app.get("/iot/alerts", summary="Alertas de sensores")
def alerts(open_only: bool = Query(True, alias="open"), warehouse_id: Optional[str] = None,
           limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db)):
    query = db.query(models.IotAlertEntity)
    if open_only:
        query = query.filter(models.IotAlertEntity.closed_at.is_(None))
    if warehouse_id:
        query = query.filter(models.IotAlertEntity.warehouse_id == warehouse_id)
    return [{"id": a.id, "warehouse_id": a.warehouse_id, "sensor_id": a.sensor_id, "sensor_type": a.sensor_type, "rule": a.rule,
             "severity": a.severity, "message": a.message, "value": a.value, "opened_at": a.opened_at, "closed_at": a.closed_at,
             "notified_at": a.notified_at}
            for a in query.order_by(models.IotAlertEntity.opened_at.desc()).limit(limit).all()]


@app.get("/iot/experiment", summary="Resultado de un experimento: lecturas guardadas, atrasadas, huecos y latencia desde una fecha")
def experiment(since: datetime, db: Session = Depends(get_db)):
    rows = (db.query(models.ReadingEntity.warehouse_id, models.ReadingEntity.measured_at, models.ReadingEntity.stored_at,
                     models.ReadingEntity.late, models.ReadingEntity.gateway_received_at)
            .filter(models.ReadingEntity.measured_at >= since).all())
    per_warehouse: dict[str, int] = {}
    for warehouse_id, *_ in rows:
        per_warehouse[warehouse_id] = per_warehouse.get(warehouse_id, 0) + 1
    end_to_end = [(r.stored_at - r.measured_at).total_seconds() * 1000 for r in rows]
    edge = [(r.gateway_received_at - r.measured_at).total_seconds() * 1000 for r in rows if r.gateway_received_at]
    first = min((r.stored_at for r in rows), default=None)
    last = max((r.stored_at for r in rows), default=None)
    span = (last - first).total_seconds() if first and last and last > first else None
    return {"stored": len(rows), "late": sum(r.late for r in rows), "per_warehouse": per_warehouse,
            "throughput_per_second": round(len(rows) / span, 2) if span else None,
            "latency_ms": {"p50": _pct(end_to_end, 50), "p95": _pct(end_to_end, 95), "p99": _pct(end_to_end, 99),
                           "max": _pct(end_to_end, 100)},
            "sensor_to_gateway_ms": {"p50": _pct(edge, 50), "p95": _pct(edge, 95)},
            "gaps": int(db.query(func.coalesce(func.sum(models.SensorStateEntity.gaps), 0)).scalar())}


def iot_metrics() -> list[str]:
    with SessionLocal() as db:
        open_alerts = db.query(func.count(models.IotAlertEntity.id)).filter(models.IotAlertEntity.closed_at.is_(None)).scalar()
        sensors = db.query(func.count(models.SensorStateEntity.sensor_id)).scalar()
    return (ingested.render() + rejected.render() + gauge("iot_open_alerts", open_alerts, "Alertas abiertas")
            + gauge("iot_sensors", sensors, "Sensores que han reportado"))


registry.add_collector(iot_metrics)
registry.add_collector(postgres_collector(engine))
registry.add_collector(rabbitmq_collector(settings.AMQP_URL, settings.RABBITMQ_MANAGEMENT_URL))


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
def metrics():
    return PlainTextResponse(registry.render(), media_type="text/plain; version=0.0.4")


@app.get("/metrics")
def metrics_summary():
    return {**registry.summary(), "instance": settings.INSTANCE_NAME}
