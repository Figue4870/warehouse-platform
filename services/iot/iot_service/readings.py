"""Contrato de una lectura de sensor (el evento de la guía) y topología de RabbitMQ de la extensión IoT."""

from datetime import datetime
from typing import Literal, Optional

from platform_common.messaging import Exchanges, QueueSpec, declare_exchanges, declare_queue
from pydantic import BaseModel, ConfigDict, Field

SENSOR_TYPES = ("temperature", "humidity", "motion", "door", "energy")
UNITS = {"temperature": "C", "humidity": "%", "motion": "bool", "door": "bool", "energy": "kW"}

# MQTT: sensors/<bodega>/<tipo>/<sensor>. El plugin MQTT de RabbitMQ lo publica en amq.topic como sensors.<bodega>.<tipo>.<sensor>
MQTT_TOPIC = "sensors/{warehouse_id}/{sensor_type}/{sensor_id}"
EDGE_QUEUE = QueueSpec(name="iot.edge", bindings=["sensors.#"], retry_delay_ms=2000, max_retries=3)

EXCHANGES = Exchanges(events="iot.events", retry="iot.retry", dlq="iot.dlq")


def queues(retry_delay_ms: int, max_retries: int) -> dict[str, QueueSpec]:
    return {name: QueueSpec(name=f"iot.{name}", bindings=["reading.#"], retry_delay_ms=retry_delay_ms, max_retries=max_retries)
            for name in ("storage", "alerts", "analytics")}


def declare_ingestion_topology(channel, retry_delay_ms: int = 3000, max_retries: int = 5) -> None:
    declare_exchanges(channel, EXCHANGES)
    for spec in queues(retry_delay_ms, max_retries).values():
        declare_queue(channel, spec, EXCHANGES)


def declare_edge_topology(channel) -> None:
    """Cola durable del gateway ligada a amq.topic (donde el plugin MQTT deja cada mensaje)."""
    channel.queue_declare(queue=EDGE_QUEUE.name, durable=True)
    for binding in EDGE_QUEUE.bindings:
        channel.queue_bind(queue=EDGE_QUEUE.name, exchange="amq.topic", routing_key=binding)


def routing_key(reading: "Reading") -> str:
    return f"reading.{reading.sensor_type}.{reading.warehouse_id}"


class Reading(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    event_id: str = Field(..., min_length=1, max_length=80)
    sequence_number: int = Field(..., ge=0)
    warehouse_id: str = Field(..., min_length=1, max_length=20)
    sensor_id: str = Field(..., min_length=1, max_length=40)
    sensor_type: Literal[SENSOR_TYPES]
    value: float
    unit: Optional[str] = Field(None, max_length=10)
    timestamp: datetime
    gateway_received_at: Optional[datetime] = None
    late: bool = False


class Batch(BaseModel):
    gateway_id: str = Field(..., max_length=60)
    readings: list[Reading] = Field(..., min_length=1, max_length=2000)
