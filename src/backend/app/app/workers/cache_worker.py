"""Cache worker: invalidación asíncrona del caché (Estrategia B del readme)."""

import logging

from app.cache import inventory_cache
from app.config import settings
from app.workers.base import parse_or_permanent, run_consumer

logger = logging.getLogger("warehouse.cache_worker")


class CacheUnavailable(Exception):
    """Redis no respondió: se reintenta más tarde (y el TTL protege mientras tanto)."""


def make_handler(cache):
    def handle(body, properties, routing_key):
        event = parse_or_permanent(body)
        if settings.CACHE_INVALIDATION_MODE not in ("async", "both"):
            return  # modo "sync": de esto ya se encargó la API
        product_id = event.payload.get("product_id")
        if product_id is None:
            return
        if not cache.invalidate(int(product_id)):
            raise CacheUnavailable("No se pudo borrar la clave en Redis")
        logger.info(
            "Clave de caché invalidada",
            extra={"event": "cache_invalidated_async", "context": {"product_id": product_id, "event_id": event.event_id, "sequence": event.sequence}},
        )

    return handle


def main() -> None:
    run_consumer("cache", make_handler(inventory_cache), "cache-worker")


if __name__ == "__main__":
    main()
