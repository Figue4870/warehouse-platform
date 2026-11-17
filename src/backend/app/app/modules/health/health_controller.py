import socket

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cache import InventoryCache, get_inventory_cache
from app.config import settings
from app.database import get_db, sql_query_counter

router = APIRouter(tags=["System"])


@router.get("/health", summary="Estado de la API y sus dependencias")
def health_check(response: Response, db: Session = Depends(get_db),
                 cache: InventoryCache = Depends(get_inventory_cache)):
    """Si PostgreSQL está caído responde 503; si solo cae Redis, la API sigue (más lenta)."""
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "down"
    redis_status = cache.status()  # ok | down | disabled

    healthy = database == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "error",
        "warehouse_id": settings.WAREHOUSE_ID,
        "database": database,
        "redis": redis_status,
    }


@router.get("/metrics", summary="Métricas para el experimento de caché (Fase 2)")
def metrics(cache: InventoryCache = Depends(get_inventory_cache)):
    return {
        "warehouse_id": settings.WAREHOUSE_ID,
        "instance": socket.gethostname(),
        # Consultas SQL que ha ejecutado ESTE proceso desde el arranque/reset
        "sql_queries_total": sql_query_counter.value,
        "cache": cache.stats(),
    }


@router.post("/metrics/reset", summary="Poner en cero los contadores antes de un experimento")
def reset_metrics(cache: InventoryCache = Depends(get_inventory_cache)):
    sql_query_counter.reset()
    cache.reset_stats()
    return metrics(cache)
