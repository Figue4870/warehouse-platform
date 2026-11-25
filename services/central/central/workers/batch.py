"""Worker central_batch: programa el reporte diario y ejecuta las corridas pedidas desde la API."""

import logging
import time

from platform_common.messaging import GracefulStop

from central import models  # noqa: F401
from central.batch import claim_next, execute, schedule_due
from central.config import settings
from central.database import SessionLocal
from central.integrations import recover_missing
from central.logging_config import setup_logging
from central.storage import LAMBDA_TRIGGERS, storage_from_settings

logger = logging.getLogger("central.batch")


def run_once(session_factory, storage) -> int:
    schedule_due(session_factory)
    executed = 0
    while (run_id := claim_next(session_factory)) is not None:
        execute(session_factory, storage, run_id)
        executed += 1
    recover_missing(session_factory, storage)
    return executed


def connect_lambda(storage) -> bool:
    """En AWS: crea el bucket si falta y le conecta la Lambda (el Learner Lab no deja hacerlo con Terraform)."""
    if not settings.LAMBDA_FUNCTION_ARN or not hasattr(storage, "ensure_notifications"):
        return True
    try:
        storage.ensure_bucket()
        if storage.ensure_notifications(settings.LAMBDA_FUNCTION_ARN, LAMBDA_TRIGGERS):
            logger.info("Bucket conectado con la Lambda", extra={"event": "lambda_connected", "context": {
                "bucket": storage.bucket, "lambda": settings.LAMBDA_FUNCTION_ARN}})
        return True
    except Exception as error:  # noqa: BLE001 - la Lambda o su permiso todavía no existen: se reintenta
        logger.warning("No se pudo conectar el bucket con la Lambda", extra={"event": "lambda_connect_failed",
                       "context": {"error": str(error)}})
        return False


def main() -> None:
    setup_logging("central-batch")
    stopper = GracefulStop()
    storage = storage_from_settings()
    logger.info("Batch programado", extra={"event": "batch_scheduler_started", "context": {
        "at": f"{settings.BATCH_HOUR:02d}:{settings.BATCH_MINUTE:02d} {settings.BATCH_TIMEZONE}",
        "destination": storage.describe()}})
    connected = False
    while not stopper.stop:
        try:
            connected = connected or connect_lambda(storage)
            run_once(SessionLocal, storage)
        except Exception as error:  # noqa: BLE001 - base caída: se reintenta en la próxima vuelta
            logger.warning("Vuelta del batch fallida", extra={"event": "batch_loop_failed", "context": {"error": str(error)}})
        for _ in range(settings.BATCH_POLL_SECONDS):
            if stopper.stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
