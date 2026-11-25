"""Worker central_shipping: pide los envíos a Servientrega y consulta los que no han avisado."""

import logging
import time

from platform_common.messaging import GracefulStop

from central import models  # noqa: F401
from central.config import settings
from central.database import SessionLocal
from central.logging_config import setup_logging
from central.shipping import poll_active, request_pending

logger = logging.getLogger("central.shipping")


def main() -> None:
    setup_logging("central-shipping")
    stopper = GracefulStop()
    logger.info("Envíos con Servientrega", extra={"event": "shipping_started", "context": {
        "enabled": settings.SHIPPING_ENABLED, "api": settings.SERVIENTREGA_API_URL}})
    while not stopper.stop:
        try:
            request_pending(SessionLocal)
            poll_active(SessionLocal)
        except Exception as error:  # noqa: BLE001 - base caída: se reintenta en la próxima vuelta
            logger.warning("Vuelta de envíos fallida", extra={"event": "shipping_loop_failed", "context": {"error": str(error)}})
        for _ in range(max(1, settings.SHIPPING_POLL_SECONDS // 2)):
            if stopper.stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
