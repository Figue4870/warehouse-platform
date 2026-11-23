"""Fase 6 — la Saga de transferencias en la central (sin RabbitMQ: comandos y respuestas simulados)."""

from datetime import datetime, timedelta, timezone

import pytest
from platform_common import commands as c

from central.saga import check_timeouts, handle_reply
from central.workers.command_relay import CommandRelay


class FakePublisher:
    def __init__(self):
        self.sent = []

    def send(self, command):
        self.sent.append((command.command_type, command.warehouse_id, dict(command.payload)))


@pytest.fixture
def saga(central_client, central_sf, apply, wh1, wh2):
    """Central con WH-001 y WH-002 conocidas (por su heartbeat) y el producto en el catálogo."""
    apply(wh1.heartbeat(0), wh2.heartbeat(0), wh1.product(sku="P-001", name="Casco", retail="32000.00"))
    publisher = FakePublisher()
    relay = CommandRelay(central_sf, publisher)

    class Saga:
        def request(self, quantity=30, **extra):
            body = {"sku": "P-001", "source_warehouse_id": "WH-001", "destination_warehouse_id": "WH-002",
                    "quantity": quantity, **extra}
            return central_client.post("/transfers", json=body)

        def publish(self):
            relay.publish_pending()
            sent, publisher.sent[:] = list(publisher.sent), []
            return sent

        def reply(self, factory, kind, transfer_id, event=None, **payload):
            event = event or factory.make(kind, {"transfer_id": transfer_id, **payload})
            with central_sf() as db:
                result = handle_reply(db, event)
                db.commit()
            return result, event

        def get(self, transfer_id):
            return central_client.get(f"/transfers/{transfer_id}").json()

        def timeouts(self, seconds_later=3600):
            with central_sf() as db:
                return check_timeouts(db, datetime.now(timezone.utc) + timedelta(seconds=seconds_later))

        def path(self, transfer_id):
            return [h["to"] for h in self.get(transfer_id)["history"] if h["from"] != h["to"]]

    saga = Saga()
    saga.publisher = publisher
    return saga


def start(saga, **extra):
    response = saga.request(**extra)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "PROCESSING" and body["state"] == "REQUESTED"
    return body["transfer_id"]


def test_camino_feliz_de_punta_a_punta(saga, wh1, wh2):
    tid = start(saga)
    assert saga.publish() == [(c.RESERVE, "WH-001", {"sku": "P-001", "quantity": 30, "source_warehouse_id": "WH-001",
                                                     "destination_warehouse_id": "WH-002"})]
    assert saga.get(tid)["state"] == "RESERVING_SOURCE"

    saga.reply(wh1, c.TRANSFER_SOURCE_RESERVED, tid)
    [(kind, warehouse, payload)] = saga.publish()
    assert (kind, warehouse) == (c.RECEIVE, "WH-002")
    assert payload["product"]["name"] == "Casco"          # el destino puede crear el producto si no lo tiene
    assert saga.get(tid)["state"] == "IN_TRANSIT"

    saga.reply(wh2, c.TRANSFER_DESTINATION_RECEIVED, tid)
    assert [s[:2] for s in saga.publish()] == [(c.COMMIT, "WH-001")]
    saga.reply(wh1, c.TRANSFER_SOURCE_COMMITTED, tid)

    detail = saga.get(tid)
    assert detail["status"] == "COMPLETED" and detail["finished_at"]
    assert saga.path(tid) == ["REQUESTED", "RESERVING_SOURCE", "SOURCE_RESERVED", "IN_TRANSIT",
                              "DESTINATION_RECEIVED", "COMPLETED"]


def test_fallar_antes_de_reservar_no_deja_nada_que_compensar(saga, wh1):
    tid = start(saga, quantity=500)
    saga.publish()
    saga.reply(wh1, c.TRANSFER_SOURCE_REJECTED, tid, reason="Stock insuficiente para reservar")
    detail = saga.get(tid)
    assert detail["state"] == "FAILED" and "insuficiente" in detail["failure_reason"]
    assert saga.publish() == []


def test_fallar_despues_de_reservar_libera_la_reserva(saga, wh1, wh2):
    tid = start(saga, simulate_failure="DESTINATION_REJECTS")
    saga.publish()
    saga.reply(wh1, c.TRANSFER_SOURCE_RESERVED, tid)
    [(_, _, receive)] = saga.publish()
    assert receive["simulate_failure"] == c.SIMULATE_DESTINATION_REJECTS
    saga.reply(wh2, c.TRANSFER_DESTINATION_REJECTED, tid, reason="Falla simulada en el destino")
    assert saga.get(tid)["state"] == "COMPENSATING"
    assert [s[:2] for s in saga.publish()] == [(c.RELEASE, "WH-001")]
    saga.reply(wh1, c.TRANSFER_SOURCE_RELEASED, tid, outcome="DONE", released_quantity=30)
    assert saga.get(tid)["state"] == "RESERVATION_RELEASED"
    assert saga.path(tid)[-2:] == ["COMPENSATING", "RESERVATION_RELEASED"]


def test_respuesta_repetida_se_ignora(saga, wh1):
    tid = start(saga)
    saga.publish()
    result, event = saga.reply(wh1, c.TRANSFER_SOURCE_RESERVED, tid)
    assert result == "applied"
    assert saga.reply(wh1, None, tid, event=event)[0] == "duplicate"
    assert len(saga.publish()) == 1                        # un solo RECEIVE


def test_timeout_del_origen_compensa_con_release(saga, wh1):
    tid = start(saga)
    saga.publish()
    assert saga.timeouts() == 1
    assert saga.get(tid)["state"] == "COMPENSATING"
    assert [s[:2] for s in saga.publish()] == [(c.RELEASE, "WH-001")]
    # La reserva llega TARDE: ya no se manda RECEIVE (el RELEASE en camino la deshará)
    assert saga.reply(wh1, c.TRANSFER_SOURCE_RESERVED, tid)[0] == "ignored"
    assert saga.publish() == []
    saga.reply(wh1, c.TRANSFER_SOURCE_RELEASED, tid, outcome="DONE", released_quantity=30)
    assert saga.get(tid)["state"] == "RESERVATION_RELEASED"


def test_destino_caido_no_se_compensa_a_ciegas(saga, wh1, wh2):
    tid = start(saga)
    saga.publish()
    saga.reply(wh1, c.TRANSFER_SOURCE_RESERVED, tid)
    saga.publish()
    assert saga.timeouts(seconds_later=10) == 0           # todavía dentro del plazo
    assert saga.timeouts() == 1
    detail = saga.get(tid)
    assert detail["state"] == "COMPENSATING" and detail["compensation_step"] == c.CANCEL_RECEIVE
    # Primero se pregunta al destino; la reserva del origen NO se libera todavía
    assert [s[:2] for s in saga.publish()] == [(c.CANCEL_RECEIVE, "WH-002")]
    saga.reply(wh2, c.TRANSFER_DESTINATION_CANCELLED, tid, cancelled=True)
    assert [s[:2] for s in saga.publish()] == [(c.RELEASE, "WH-001")]
    saga.reply(wh1, c.TRANSFER_SOURCE_RELEASED, tid, outcome="DONE", released_quantity=30)
    assert saga.get(tid)["state"] == "RESERVATION_RELEASED"
