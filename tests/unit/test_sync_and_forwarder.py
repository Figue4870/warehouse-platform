"""Fase 7 (lado bodega): endpoints de sincronización, heartbeat y forwarder."""

from types import SimpleNamespace

import pytest


from app.workers.central_forwarder import CentralForwarder, CentralUnavailable
from app.workers.relay import OutboxRelay


class RecordingPublisher:
    def __init__(self, fail=False):
        self.published, self.fail = [], fail

    def publish(self, **kwargs):
        if self.fail:
            raise ConnectionError("central caída")
        self.published.append(kwargs)


def publish_all(session_factory):
    OutboxRelay(session_factory, RecordingPublisher()).run_once()


def test_sync_events_devuelve_lo_posterior_a_una_secuencia(client, create_product, move, session_factory):
    product = create_product(sku="SYN-1")
    move(product["id"], "RECEIPT", 5)
    move(product["id"], "SHIPMENT", 2)
    publish_all(session_factory)
    data = client.get("/sync/events", params={"after_sequence": 1}).json()
    assert data["last_sequence"] == 3 and data["oldest_available_sequence"] == 1
    assert [e["sequence"] for e in data["events"]] == [2, 3]
    assert data["events"][1]["payload"]["quantity_after"] == 3


class FakeChannel:
    def __init__(self):
        self.acks = []

    def basic_ack(self, delivery_tag):
        self.acks.append(delivery_tag)


def message(routing_key="inventory.movement.recorded"):
    props = SimpleNamespace(headers={"sequence": 5}, message_id="evt-1", correlation_id="COR-1", type="X", expiration=None, delivery_mode=2)
    return SimpleNamespace(delivery_tag=9, routing_key=routing_key), props


def test_forwarder_reenvia_y_solo_despues_hace_ack():
    central = RecordingPublisher()
    channel = FakeChannel()
    method, props = message()
    CentralForwarder("amqp://local", central, "WH-001")._on_message(channel, method, props, b"{}")
    [sent] = central.published
    assert sent["routing_key"] == "WH-001.inventory.movement.recorded" and sent["message_id"] == "evt-1"
    assert sent["headers"]["warehouse_id"] == "WH-001"
    assert channel.acks == [9]


def test_si_la_central_esta_caida_no_hay_ack_y_el_mensaje_se_queda():
    channel = FakeChannel()
    method, props = message()
    with pytest.raises(CentralUnavailable):
        CentralForwarder("amqp://local", RecordingPublisher(fail=True), "WH-001")._on_message(channel, method, props, b"{}")
    assert channel.acks == []  # sin ack: RabbitMQ lo conserva en la cola local
