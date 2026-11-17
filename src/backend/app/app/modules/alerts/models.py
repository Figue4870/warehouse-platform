from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base
from app.modules.shared.utils import schemas_utils

ALERT_TYPES = (
    "OUT_OF_STOCK",  # el producto se agotó (disponible llegó a 0)
    "DISCREPANCY",   # se registró un ajuste por desfase/incongruencia
)

_types_sql = ", ".join(f"'{t}'" for t in ALERT_TYPES)


class InventoryAlertEntity(Base):
    """Log automático de eventos importantes del inventario (requisito del PDF)."""

    __tablename__ = "inventory_alerts"
    __table_args__ = (CheckConstraint(f"type IN ({_types_sql})", name="ck_alerts_type"),)

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    warehouse_id = Column(String(20), nullable=False)
    type = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    movement_id = Column(Integer, ForeignKey("inventory_movements.id"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=schemas_utils.current_utc_time)
