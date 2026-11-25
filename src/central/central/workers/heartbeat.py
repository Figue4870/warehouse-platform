"""Worker central_heartbeat: cada HEARTBEAT_POLL_SECONDS le pregunta a cada bodega con un reto cifrado."""

import logging
import time

from platform_common.messaging import GracefulStop

from central import models  # noqa: F401
from central.config import settings, warehouse_registry
from central.database import SessionLocal
from central.heartbeat import OK, HeartbeatClient, poll_once
from central.logging_config import setup_logging

logger = logging.getLogger("central.heartbeat")


def main() -> None:
    setup_logging("central-heartbeat")
    stopper = GracefulStop()
    client = HeartbeatClient()
    registry = warehouse_registry()
    logger.info("Monitor de bodegas iniciado", extra={"event": "heartbeat_monitor_started", "context": {
        "every_seconds": settings.HEARTBEAT_POLL_SECONDS, "warehouses": sorted(registry),
        "without_secret": sorted(wh for wh, entry in registry.items() if not entry.get("secret"))}})
    last = {}  # último resultado por bodega: solo se escribe en el log cuando cambia
    while not stopper.stop:
        try:
            for result in poll_once(SessionLocal, client, registry):
                if last.get(result.warehouse_id) != result.outcome:
                    level = logging.INFO if result.outcome == OK else logging.WARNING
                    logger.log(level, "Cambio de estado de una bodega", extra={"event": "heartbeat_state_changed", "context": {
                        "warehouse_id": result.warehouse_id, "outcome": result.outcome, "detail": result.detail}})
                    last[result.warehouse_id] = result.outcome
        except Exception as error:  # noqa: BLE001
            logger.warning("Vuelta de heartbeat fallida", extra={"event": "heartbeat_poll_failed", "context": {"error": str(error)}})
        for _ in range(settings.HEARTBEAT_POLL_SECONDS):
            if stopper.stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
