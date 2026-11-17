"""Fase 3: el relay publica el outbox SIN perder ni desordenar eventos."""


import pytest

from platform_common.events import parse_event

from app.events.models import OutboxEventEntity
from app.workers.relay import OutboxRelay


class FakePublisher:
    """Imita RabbitMQ. fail_on = número de publicación (1, 2, 3...) que debe fallar."""

    def __init__(self, fail_on=None):
        self.published = []
        self.fail_on = set(fail_on or [])
        self.calls = 0

    def publish(self, **kwargs):
        self.calls += 1
        if self.calls in self.fail_on:
            raise ConnectionError("RabbitMQ caído (simulado)")
        self.published.append(kwargs)


def outbox(session_factory):
    with session_factory() as db:
        return db.query(OutboxEventEntity).order_by(OutboxEventEntity.id).all()


@pytest.fixture
def five_events(client, create_product, move):
    product = create_product()           # 1 evento
    for _ in range(4):
        move(product["id"], "RECEIPT", 1)  # 4 eventos
    return product


def test_publica_en_orden_con_secuencia_sin_huecos(five_events, session_factory):
    publisher = FakePublisher()
    assert OutboxRelay(session_factory, publisher).run_once() == 5
    sequences = [parse_event(p["body"]).sequence for p in publisher.published]
    assert sequences == [1, 2, 3, 4, 5]
    assert all(row.published_at is not None for row in outbox(session_factory))
    first = parse_event(publisher.published[0]["body"])
    assert first.event_type == "PRODUCT_CREATED" and first.source == "warehouse-WH-TEST"
    assert publisher.published[0]["routing_key"] == "product.created"
    assert publisher.published[0]["message_id"] == first.event_id  # message_id = event_id (idempotencia)


def test_si_rabbitmq_falla_nada_se_pierde_ni_se_desordena(five_events, session_factory):
    publisher = FakePublisher(fail_on=[3])
    relay = OutboxRelay(session_factory, publisher)
    with pytest.raises(ConnectionError):
        relay.run_once()
    rows = outbox(session_factory)
    assert [r.published_at is not None for r in rows] == [True, True, False, False, False]
    assert rows[2].last_error.startswith("ConnectionError") and rows[2].attempts == 1

    # RabbitMQ "vuelve": se publica el resto, con las MISMAS secuencias y en orden
    assert relay.run_once() == 3
    assert [parse_event(p["body"]).sequence for p in publisher.published] == [1, 2, 3, 4, 5]
    assert all(r.published_at is not None for r in outbox(session_factory))
