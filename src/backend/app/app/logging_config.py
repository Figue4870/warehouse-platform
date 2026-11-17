import json
import logging
import sys
from datetime import datetime, timezone

from app.config import settings


class JsonFormatter(logging.Formatter):
    """Escribe cada log como una línea JSON (log estructurado)."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "warehouse-api",
            "warehouse_id": settings.WAREHOUSE_ID,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event:
            data["event"] = event
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            data.update(context)
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    logger = logging.getLogger("warehouse")
    if logger.handlers:  # evita duplicar handlers si se llama dos veces
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
