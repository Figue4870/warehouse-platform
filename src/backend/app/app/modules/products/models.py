from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, Numeric, String, Text, UniqueConstraint

from app.database import Base
from app.modules.shared.utils import schemas_utils


class ProductEntity(Base):
    """Producto del catálogo de la bodega."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_products_sku"),
        CheckConstraint("supplier_cost >= 0", name="ck_products_supplier_cost"),
        CheckConstraint("retail_price >= 0", name="ck_products_retail_price"),
        CheckConstraint("wholesale_price >= 0", name="ck_products_wholesale_price"),
    )

    id = Column(Integer, primary_key=True)
    sku = Column(String(64), nullable=False)  # código universal
    name = Column(String(200), nullable=False)                          # nombre universal
    description = Column(Text)
    category = Column(String(100))
    unit = Column(String(30), nullable=False)
    supplier = Column(String(200))                   # proveedor asociado
    lot = Column(String(100))                        # lote
    supplier_cost = Column(Numeric(14, 2))           # costo / precio del proveedor
    retail_price = Column(Numeric(14, 2))            # precio de venta en tienda
    wholesale_price = Column(Numeric(14, 2))         # precio mayoristas / terceros
    is_active = Column(Boolean, nullable=False, default=True)  # False = dado de baja (borrado lógico)
    created_at = Column(DateTime(timezone=True), nullable=False, default=schemas_utils.current_utc_time)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=schemas_utils.current_utc_time,
        onupdate=schemas_utils.current_utc_time,
    )
