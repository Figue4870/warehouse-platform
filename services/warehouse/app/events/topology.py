"""Topología de RabbitMQ dentro de una bodega (Fase 3)."""

from platform_common.messaging import Exchanges, QueueSpec, declare_exchanges, declare_queue

from app.config import settings

EXCHANGES = Exchanges(events="warehouse.events", retry="warehouse.retry", dlq="warehouse.dlq")


def _spec(name: str, bindings: list[str], max_retries: int | None = None) -> QueueSpec:
    return QueueSpec(
        name=name,
        bindings=bindings,
        retry_delay_ms=settings.WORKER_RETRY_DELAY_MS,
        max_retries=settings.WORKER_MAX_RETRIES if max_retries is None else max_retries,
    )


QUEUES = {
    "cache": _spec("wh.cache-invalidation", ["inventory.movement.recorded", "product.deactivated"]),
    "notifications": _spec("wh.notifications", ["stock.depleted", "stock.discrepancy", "inventory.inconsistency"]),
    "reports": _spec("wh.reports", ["report.requested"], max_retries=3),
    "audit": _spec("wh.audit", ["product.#", "inventory.#", "stock.#", "report.#", "transfer.#"]),
}


CENTRAL_FORWARD = QueueSpec(name="wh.central-forward", bindings=["#"], with_retry=False)


def central_enabled() -> bool:
    return bool(settings.CENTRAL_AMQP_URL)


def declare_topology(channel) -> None:
    """Idempotente: declarar algo que ya existe no lo cambia. La llaman el relay y todos los workers."""
    declare_exchanges(channel, EXCHANGES)
    for spec in QUEUES.values():
        declare_queue(channel, spec, EXCHANGES)
    if central_enabled():
        declare_queue(channel, CENTRAL_FORWARD, EXCHANGES)
