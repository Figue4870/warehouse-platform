"""Topología del RabbitMQ central (Fase 7)."""

from platform_common.messaging import Exchanges, QueueSpec, declare_exchanges, declare_queue

CENTRAL_EXCHANGES = Exchanges(events="central.events", retry="central.retry", dlq="central.dlq")

PROJECTION_QUEUE = QueueSpec(name="central.projection", bindings=["#"], retry_delay_ms=5000, max_retries=5)


def central_routing_key(warehouse_id: str, routing_key: str) -> str:
    return f"{warehouse_id}.{routing_key}"


def declare_central_topology(channel) -> None:
    declare_exchanges(channel, CENTRAL_EXCHANGES)
    declare_queue(channel, PROJECTION_QUEUE, CENTRAL_EXCHANGES)
