import json
import logging
import time

from platform_common.commands import COMMAND_EXCHANGES, declare_commands
from platform_common.messaging import GracefulStop, ReliablePublisher
from sqlalchemy import select

from central.config import settings
from central.database import SessionLocal
from central.logging_config import setup_logging
from central.workers.saga import declare as declare_saga
from central.saga import Command, Transfer, check_timeouts, now, transition

logger = logging.getLogger("central.command_relay")


def publish_one(publisher):
    with SessionLocal.begin() as db:
        command = db.scalar(select(Command).where(Command.published_at.is_(None))
                            .order_by(Command.id).limit(1).with_for_update(skip_locked=True))
        if command is None:
            return False
        publisher.publish(COMMAND_EXCHANGES.events, command.warehouse,
                          json.dumps(command.payload).encode(), message_id=str(command.id))
        transfer = db.get(Transfer, command.transfer_id, with_for_update=True)
        if command.step == "RESERVE" and transfer.state == "REQUESTED":
            deadline = transfer.deadline
            transition(db, transfer, "RESERVING_SOURCE")
            transfer.deadline = deadline
        command.published_at = now()
    return True


def main():
    setup_logging("central-command-relay")
    stopper = GracefulStop()
    with SessionLocal() as db:
        warehouses = db.scalars(select(Transfer.source).distinct()).all()
    # Se declara también al publicar, incluso si todavía no hay consumidor.
    publisher = ReliablePublisher(settings.AMQP_URL, "central-command-relay",
                                  on_connect=lambda channel: (declare_saga(channel), declare_commands(channel, warehouses)))
    try:
        while not stopper.stop:
            try:
                with SessionLocal.begin() as db:
                    check_timeouts(db)
                    commands = db.scalars(select(Command.warehouse).where(Command.published_at.is_(None)).distinct()).all()
                if set(commands) - set(warehouses):
                    warehouses = sorted(set(warehouses) | set(commands))
                    publisher.close()
                for _ in range(100):
                    if stopper.stop or not publish_one(publisher):
                        break
            except Exception:
                logger.exception("No se pudieron publicar comandos")
            time.sleep(1)
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
