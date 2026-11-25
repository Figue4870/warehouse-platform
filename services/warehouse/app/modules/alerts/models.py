from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base
from app.modules.shared.utils import schemas_utils

ALERT_TYPES = (
    "OUT_OF_STOCK",   # el producto se agotó (disponible llegó a 0)
    "DISCREPANCY",    # desfase reportado por un operador o ajuste registrado
    "INCONSISTENCY",  # desfase detectado automáticamente (el saldo no coincide con su historial)
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
    notified_at = Column(DateTime(timezone=True))  # Fase 3: cuándo el worker avisó al administrador
    notification_channel = Column(String(40))
    acknowledged_at = Column(DateTime(timezone=True))
    acknowledged_by = Column(String(100))
    created_at = Column(DateTime(timezone=True), nullable=False, default=schemas_utils.current_utc_time)
