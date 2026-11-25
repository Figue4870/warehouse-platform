"""Command worker (Fase 6) — ejecuta en esta bodega los pasos de la Saga de transferencias."""

import logging
import time

from platform_common.central_topology import COMMAND_EXCHANGES, command_queue, declare_command_topology
from platform_common.commands import (
    CATALOG_UPSERT,
    PLATFORM_COMMANDS,
    PRODUCT_REQUEST_RESOLVED,
    InvalidCommandError,
    parse_command,
)
from platform_common.correlation import reset_correlation_id, set_correlation_id
from platform_common.messaging import Consumer, GracefulStop, PermanentError

import app.all_models  # noqa: F401
from app.config import settings
from app.database import SessionLocal
from app.logging_config import setup_logging
from app.modules.catalog.catalog_service import CatalogService
from app.modules.requests.requests_service import RequestService
from app.modules.transfers.transfer_commands import TransferCommandService

logger = logging.getLogger("warehouse.command_worker")


def make_handler(session_factory, warehouse_id: str, cache=None):
    def handle(body, properties, routing_key):
        try:
            command = parse_command(body)
        except InvalidCommandError as error:
            raise PermanentError(str(error)) from error
        if command.warehouse_id != warehouse_id:
            raise PermanentError(f"El comando es para {command.warehouse_id}, no para {warehouse_id}")
        token = set_correlation_id(command.correlation_id)
        try:
            with session_factory() as db:
                if command.command_type in PLATFORM_COMMANDS:
                    handle_platform_command(db, command)
                else:
                    TransferCommandService(db, cache).handle(command)
        finally:
            reset_correlation_id(token)

    return handle


def handle_platform_command(db, command) -> str:
    """Comandos idempotentes: aplicarlos dos veces deja el mismo resultado."""
    if command.command_type == CATALOG_UPSERT:
        outcome = CatalogService(db).upsert(command.payload)
    elif command.command_type == PRODUCT_REQUEST_RESOLVED:
        outcome = RequestService(db).resolve_from_central(command.payload)
    else:
        raise PermanentError(f"Comando de plataforma sin manejador: {command.command_type}")
    db.commit()
    logger.info("Comando de la plataforma aplicado", extra={"event": "platform_command_applied", "context": {
        "command": command.command_type, "outcome": outcome, "command_id": command.command_id}})
    return outcome


def main() -> None:
    setup_logging(service="command-worker")
    if not settings.CENTRAL_AMQP_URL:
        logger.error("CENTRAL_AMQP_URL vacío: sin central no hay transferencias", extra={"event": "command_worker_disabled"})
        stopper = GracefulStop()
        while not stopper.stop:  # queda inactivo (en vez de reiniciarse en bucle en Docker)
            time.sleep(1)
        return
    spec = command_queue(settings.WAREHOUSE_ID)
    logger.info("Esperando comandos de la central", extra={"event": "command_worker_started", "context": {"queue": spec.name}})
    Consumer(
        url=settings.CENTRAL_AMQP_URL,
        connection_name=f"command-worker-{settings.WAREHOUSE_ID}",
        spec=spec,
        exchanges=COMMAND_EXCHANGES,
        handler=make_handler(SessionLocal, settings.WAREHOUSE_ID),
        declare_topology=lambda channel: declare_command_topology(channel, settings.WAREHOUSE_ID),
        prefetch=1,  # un comando a la vez: más fácil de seguir en los logs y conserva el orden
    ).run()


if __name__ == "__main__":
    main()
