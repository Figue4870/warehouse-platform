from sqlalchemy import JSON, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.database import Base
from app.modules.shared.utils import schemas_utils

REQUEST_TYPES = ("NEW_PRODUCT", "ADJUSTMENT")
# PENDING: espera al administrador de bodega. FORWARDED: aprobada aquí, espera a la central (solo NEW_PRODUCT)
REQUEST_STATUSES = ("PENDING", "FORWARDED", "APPROVED", "REJECTED")

_types_sql = ", ".join(f"'{t}'" for t in REQUEST_TYPES)
_statuses_sql = ", ".join(f"'{s}'" for s in REQUEST_STATUSES)


class OperationRequestEntity(Base):
    """Solicitud de un registrador: crear un producto nuevo o ajustar un desfase de inventario."""

    __tablename__ = "operation_requests"
    __table_args__ = (
        CheckConstraint(f"type IN ({_types_sql})", name="ck_requests_type"),
        CheckConstraint(f"status IN ({_statuses_sql})", name="ck_requests_status"),
        Index("ix_requests_status", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(String(20), nullable=False)
    type = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="PENDING")
    product_id = Column(Integer, ForeignKey("products.id"))
    details = Column(JSON, nullable=False)
    created_by = Column(String(100), nullable=False)
    decided_by = Column(String(100))
    decided_at = Column(DateTime(timezone=True))
    decision_note = Column(Text)
    alert_id = Column(Integer, ForeignKey("inventory_alerts.id"))
    movement_id = Column(Integer, ForeignKey("inventory_movements.id"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=schemas_utils.current_utc_time)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=schemas_utils.current_utc_time,
                        onupdate=schemas_utils.current_utc_time)

    @property
    def code(self) -> str:
        return f"REQ-{self.warehouse_id}-{self.id:05d}"
