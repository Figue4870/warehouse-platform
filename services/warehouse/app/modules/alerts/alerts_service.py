import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.modules.alerts.alerts_repository import AlertRepository
from app.modules.shared.utils import schemas_utils

logger = logging.getLogger("warehouse.alerts")

OUT_OF_STOCK = "OUT_OF_STOCK"
DISCREPANCY = "DISCREPANCY"
INCONSISTENCY = "INCONSISTENCY"


class AlertService:
    """Registra alertas automáticas del inventario."""

    def __init__(self, db: Session):
        self.repo = AlertRepository(db)

    def register(self, product_id: int, alert_type: str, message: str, movement_id: Optional[int]):
        return self.repo.create(product_id, alert_type, message, movement_id)

    def list_alerts(self, limit: int, alert_type: Optional[str], product_id: Optional[int], pending: bool = False):
        return self.repo.list(limit, alert_type, product_id, pending)

    def acknowledge(self, alert_id: int, username: str):
        alert = self.repo.get(alert_id)
        if alert is None:
            return None
        if alert.acknowledged_at is None:
            alert.acknowledged_at, alert.acknowledged_by = schemas_utils.current_utc_time(), username
            self.repo.db.commit()
            self.repo.db.refresh(alert)
        return alert
