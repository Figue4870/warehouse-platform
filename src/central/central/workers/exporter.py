import logging
import time

from platform_common.messaging import GracefulStop

from central.analytics.exporter import Storage, export_movements, export_transfers, snapshot_inventory
from central.config import settings
from central.database import SessionLocal
from central.logging_config import setup_logging

logger = logging.getLogger("central.exporter")


def main():
    setup_logging("central-exporter")
    stopper = GracefulStop()
    storage = Storage(settings.EXPORT_DIRECTORY, settings.EXPORT_S3_BUCKET)
    next_snapshot = 0
    while not stopper.stop:
        try:
            with SessionLocal() as db:
                export_movements(db, storage)
                export_transfers(db, storage)
                if time.monotonic() >= next_snapshot:
                    snapshot_inventory(db, storage)
                    next_snapshot = time.monotonic() + 3600
        except Exception:
            logger.exception("Falló el lote de exportación")
        for _ in range(60):
            if stopper.stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
