"""Fase 11: métricas de la bodega (API, Redis, RabbitMQ y PostgreSQL) en formato Prometheus."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from platform_common.metrics import Registry, gauge, instrument_sqlalchemy, postgres_collector, rabbitmq_collector
from sqlalchemy import func

from app.cache import inventory_cache
from app.config import settings
from app.database import SessionLocal, engine
from app.events.models import OutboxEventEntity

registry = Registry(f"warehouse-api-{settings.WAREHOUSE_ID}")
instrument_sqlalchemy(engine, registry)
router = APIRouter(tags=["System"])


def redis_metrics() -> list[str]:
    stats = inventory_cache.stats()
    lines = gauge("cache_enabled", int(stats["enabled"]), "Caché activado (CACHE_ENABLED)")
    lines += gauge("cache_up", int(stats["status"] == "ok"), "Redis responde")
    if stats["status"] != "ok":
        return lines
    info = inventory_cache.client.info()
    return lines + [
        "# TYPE cache_hits_total counter", f"cache_hits_total {stats['hits']}",
        "# TYPE cache_misses_total counter", f"cache_misses_total {stats['misses']}",
        *gauge("cache_hit_ratio", stats["hit_ratio"], "Aciertos / consultas al caché"),
        *gauge("redis_memory_used_bytes", info.get("used_memory"), "Memoria usada por Redis"),
        *gauge("redis_connected_clients", info.get("connected_clients"), "Clientes conectados a Redis"),
    ]


def outbox_metrics() -> list[str]:
    with SessionLocal() as db:
        pending = db.query(func.count(OutboxEventEntity.id)).filter(OutboxEventEntity.published_at.is_(None)).scalar()
    return gauge("outbox_pending_events", pending, "Eventos guardados que aún no llegan a RabbitMQ")


registry.add_collector(redis_metrics)
registry.add_collector(outbox_metrics)
registry.add_collector(postgres_collector(engine))
registry.add_collector(rabbitmq_collector(settings.AMQP_URL, settings.RABBITMQ_MANAGEMENT_URL))


@router.get("/metrics/prometheus", response_class=PlainTextResponse, summary="Métricas en formato Prometheus")
def metrics():
    return PlainTextResponse(registry.render(), media_type="text/plain; version=0.0.4")
