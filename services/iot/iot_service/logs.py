import json
import logging
import sys
from datetime import datetime, timezone

from platform_common.correlation import get_correlation_id


class JsonFormatter(logging.Formatter):
    service = "iot"

    def format(self, record: logging.LogRecord) -> str:
        data = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname, "service": self.service,
                "logger": record.name, "message": record.getMessage()}
        if correlation_id := get_correlation_id():
            data["correlation_id"] = correlation_id
        if getattr(record, "event", None):
            data["event"] = record.event
        if isinstance(getattr(record, "context", None), dict):
            data.update(record.context)
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(service: str, level: int = logging.INFO) -> None:
    JsonFormatter.service = service
    for name in ("iot", "platform"):
        logger = logging.getLogger(name)
        if logger.handlers:
            continue
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
