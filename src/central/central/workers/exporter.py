"""Exporter: cada EXPORT_INTERVAL_SECONDS copia lo nuevo del modelo de lectura a S3."""

import logging
import time

from platform_common.messaging import GracefulStop

from central import models  # noqa: F401
from central.analytics.exporter import Exporter
from central.analytics.storage import storage_from_settings
from central.config import settings
from central.database import SessionLocal
from central.logging_config import setup_logging

logger = logging.getLogger("central.exporter")


def main() -> None:
    setup_logging("central-exporter")
    stopper = GracefulStop()
    storage = storage_from_settings()
    exporter = Exporter(SessionLocal, storage, settings.EXPORT_BATCH_SIZE, settings.EXPORT_SNAPSHOT_SECONDS)
    logger.info("Exportador iniciado", extra={"event": "exporter_started", "context": {
        "destination": storage.describe(), "every_seconds": settings.EXPORT_INTERVAL_SECONDS}})
    while not stopper.stop:
        try:
            result = exporter.run_once()
            if any(result.values()):
                logger.info("Datos exportados", extra={"event": "export_done", "context": result})
        except Exception as error:  # noqa: BLE001 - S3 o la base caídos: se reintenta (lo pendiente sigue pendiente)
            logger.warning("Exportación fallida; se reintentará", extra={"event": "export_failed", "context": {"error": str(error)}})
        for _ in range(settings.EXPORT_INTERVAL_SECONDS):
            if stopper.stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
