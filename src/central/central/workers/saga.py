from platform_common.central_topology import CENTRAL_EXCHANGES, declare_central_topology
from platform_common.events import InvalidEventError, parse_event
from platform_common.messaging import Consumer, PermanentError, QueueSpec, declare_queue

from central.config import settings
from central.database import SessionLocal
from central.logging_config import setup_logging
from central.saga import handle_reply

QUEUE = QueueSpec("central.saga", ["*.transfer.reply"], 5000, 10)


def declare(channel):
    declare_central_topology(channel)
    declare_queue(channel, QUEUE, CENTRAL_EXCHANGES)


def receive(body, properties, routing_key):
    try:
        event = parse_event(body)
        if event.event_type != "TRANSFER_REPLY" or not event.warehouse_id:
            raise ValueError("Evento incorrecto")
        payload = event.payload
        prefix, _, number = payload["transfer_id"].partition("-")
        if prefix != "TR" or not number.isascii() or not number.isdecimal() or len(number) > 10:
            raise ValueError("ID inválido")
        if payload["step"] not in {"RESERVE", "RECEIVE", "COMMIT", "RELEASE", "CANCEL_RECEIVE"}:
            raise ValueError("Paso inválido")
        if payload["outcome"] not in {"OK", "FAILED", "NOOP", "ALREADY_RECEIVED"}:
            raise ValueError("Resultado inválido")
    except (InvalidEventError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise PermanentError("Respuesta de transferencia inválida") from error
    with SessionLocal.begin() as db:
        handle_reply(db, event)


if __name__ == "__main__":
    setup_logging("central-saga")
    Consumer(settings.AMQP_URL, "central-saga", QUEUE, CENTRAL_EXCHANGES,
             receive, declare, prefetch=1).run()
