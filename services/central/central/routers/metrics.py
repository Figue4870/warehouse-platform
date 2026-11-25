"""Fase 11: métricas de la central (API, base, RabbitMQ central y estado de la plataforma)."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from platform_common.metrics import Registry, gauge, instrument_sqlalchemy, postgres_collector, rabbitmq_collector
from sqlalchemy import func

from central.config import settings
from central.database import SessionLocal, engine
from central.models import BatchRunEntity, ContractEntity, ShipmentEntity, TransferCommandEntity, TransferEntity, WarehouseEntity
from central.projection import as_aware, now_utc
from central.status import warehouse_status

registry = Registry("central-api")
instrument_sqlalchemy(engine, registry)
router = APIRouter(tags=["Health"])


def platform_metrics() -> list[str]:
    now = now_utc()
    with SessionLocal() as db:
        statuses = [warehouse_status(w, now) for w in db.query(WarehouseEntity).all()]
        transfers = db.query(TransferEntity.state, func.count()).group_by(TransferEntity.state).all()
        shipments = db.query(ShipmentEntity.status, func.count()).group_by(ShipmentEntity.status).all()
        contracts = db.query(ContractEntity.status, func.count()).group_by(ContractEntity.status).all()
        commands = db.query(func.count(TransferCommandEntity.id)).filter(TransferCommandEntity.published_at.is_(None)).scalar()
        last_batch = (db.query(func.max(BatchRunEntity.finished_at))
                      .filter(BatchRunEntity.status.in_(("SUCCEEDED", "PARTIAL"))).scalar())
    lines = ["# TYPE warehouse_online gauge", "# TYPE warehouse_events_missing gauge", "# TYPE warehouse_outbox_pending gauge",
             "# TYPE warehouse_seconds_since_contact gauge"]
    for s in statuses:
        label = f'{{warehouse="{s["warehouse_id"]}"}}'
        lines += [f"warehouse_online{label} {int(s['status'] == 'ONLINE')}", f"warehouse_events_missing{label} {s['events_missing']}",
                  f"warehouse_outbox_pending{label} {s['outbox_pending'] or 0}"]
        if s["seconds_since_contact"] is not None:
            lines.append(f"warehouse_seconds_since_contact{label} {s['seconds_since_contact']}")
    lines += ["# TYPE saga_transfers gauge"] + [f'saga_transfers{{state="{state}"}} {count}' for state, count in transfers]
    lines += ["# TYPE shipments gauge"] + [f'shipments{{status="{state}"}} {count}' for state, count in shipments]
    lines += ["# TYPE contracts gauge"] + [f'contracts{{status="{state}"}} {count}' for state, count in contracts]
    lines += gauge("central_commands_pending", commands, "Comandos del outbox de la central sin publicar")
    if last_batch:
        lines += gauge("batch_last_success_age_seconds", round((now - as_aware(last_batch)).total_seconds()),
                       "Segundos desde el último reporte diario terminado")
    return lines


registry.add_collector(platform_metrics)
registry.add_collector(postgres_collector(engine))
registry.add_collector(rabbitmq_collector(settings.AMQP_URL, settings.RABBITMQ_MANAGEMENT_URL))


@router.get("/metrics", summary="Resumen de la API central: RPS, P50/P95/P99, errores y latencia de la base")
def metrics_summary():
    return {**registry.summary(), "instance": settings.INSTANCE_NAME}


@router.get("/metrics/prometheus", response_class=PlainTextResponse, summary="Métricas en formato Prometheus")
def metrics():
    return PlainTextResponse(registry.render(), media_type="text/plain; version=0.0.4")
