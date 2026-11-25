"""Topología del RabbitMQ central (Fase 7)."""

from platform_common.messaging import Exchanges, QueueSpec, declare_exchanges, declare_queue

CENTRAL_EXCHANGES = Exchanges(events="central.events", retry="central.retry", dlq="central.dlq")

PROJECTION_QUEUE = QueueSpec(name="central.projection", bindings=["#"], retry_delay_ms=5000, max_retries=5)
SAGA_QUEUE = QueueSpec(name="central.saga", bindings=["*.transfer.#"], retry_delay_ms=5000, max_retries=5)


def central_routing_key(warehouse_id: str, routing_key: str) -> str:
    return f"{warehouse_id}.{routing_key}"


def declare_central_topology(channel) -> None:
    """Declara las DOS colas que leen de central.events: así ningún evento llega sin su cola."""
    declare_exchanges(channel, CENTRAL_EXCHANGES)
    declare_queue(channel, PROJECTION_QUEUE, CENTRAL_EXCHANGES)
    declare_queue(channel, SAGA_QUEUE, CENTRAL_EXCHANGES)


# Fase 6: comandos de la Saga (central -> bodegas), una cola durable por bodega.
# Las respuestas vuelven por el outbox de la bodega como cualquier evento.

COMMAND_EXCHANGES = Exchanges(events="central.commands", retry="central.commands.retry", dlq="central.commands.dlq")

# Central y bodega declaran la cola con los mismos argumentos (si no, PRECONDITION_FAILED).
COMMAND_RETRY_DELAY_MS = 5000
COMMAND_MAX_RETRIES = 10


def command_queue(warehouse_id: str) -> QueueSpec:
    return QueueSpec(name=f"commands.{warehouse_id}", bindings=[warehouse_id],
                     retry_delay_ms=COMMAND_RETRY_DELAY_MS, max_retries=COMMAND_MAX_RETRIES)


def declare_command_topology(channel, warehouse_id: str) -> None:
    declare_exchanges(channel, COMMAND_EXCHANGES)
    declare_queue(channel, command_queue(warehouse_id), COMMAND_EXCHANGES)
