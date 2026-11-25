"""Worker central_heartbeat: cada HEARTBEAT_POLL_SECONDS le pregunta a cada bodega con un reto cifrado."""

import logging
import time

from platform_common.messaging import GracefulStop

from platform_common.auth import ADMIN_GLOBAL

from central import models  # noqa: F401
from central.config import settings
from central.database import SessionLocal
from central.heartbeat import NO_SECRET, OK, REJECTED, UNREACHABLE, HeartbeatClient, full_registry, poll_once
from central.logging_config import setup_logging
from central.notifications import CRITICAL, INFO, notify

logger = logging.getLogger("central.heartbeat")
PROBLEM = {UNREACHABLE: "no responde", REJECTED: "respondió sin autenticarse", NO_SECRET: "no tiene clave de heartbeat configurada"}


def notify_change(result, previous) -> None:
    """Avisa al administrador global cuando una bodega deja de responder bien o se recupera."""
    if result.outcome == OK and previous in (None, OK):
        return
    with SessionLocal() as db:
        if result.outcome == OK:
            notify(db, ADMIN_GLOBAL, "WAREHOUSE_RECOVERED", f"{result.warehouse_id} volvió a responder", "", INFO,
                   result.warehouse_id)
        else:
            notify(db, ADMIN_GLOBAL, "WAREHOUSE_UNHEALTHY", f"{result.warehouse_id} {PROBLEM.get(result.outcome, result.outcome)}", result.detail,
                   CRITICAL, result.warehouse_id)
        db.commit()


def main() -> None:
    setup_logging("central-heartbeat")
    stopper = GracefulStop()
    client = HeartbeatClient()
    registry = full_registry(SessionLocal)
    logger.info("Monitor de bodegas iniciado", extra={"event": "heartbeat_monitor_started", "context": {
        "every_seconds": settings.HEARTBEAT_POLL_SECONDS, "warehouses": sorted(registry),
        "without_secret": sorted(wh for wh, entry in registry.items() if not entry.get("secret"))}})
    last = {}  # último resultado por bodega: solo se escribe en el log cuando cambia
    while not stopper.stop:
        try:
            registry = full_registry(SessionLocal)
            for result in poll_once(SessionLocal, client, registry):
                previous = last.get(result.warehouse_id)
                if previous != result.outcome:
                    level = logging.INFO if result.outcome == OK else logging.WARNING
                    logger.log(level, "Cambio de estado de una bodega", extra={"event": "heartbeat_state_changed", "context": {
                        "warehouse_id": result.warehouse_id, "outcome": result.outcome, "detail": result.detail}})
                    notify_change(result, previous)
                    last[result.warehouse_id] = result.outcome
        except Exception as error:  # noqa: BLE001
            logger.warning("Vuelta de heartbeat fallida", extra={"event": "heartbeat_poll_failed", "context": {"error": str(error)}})
        for _ in range(settings.HEARTBEAT_POLL_SECONDS):
            if stopper.stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
