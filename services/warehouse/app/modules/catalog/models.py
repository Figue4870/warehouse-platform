from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text

from app.database import Base
from app.modules.shared.utils import schemas_utils


class UniversalProductEntity(Base):
    """Copia local del catálogo universal que administra la central (lo reciben los comandos CATALOG_UPSERT)."""

    __tablename__ = "universal_products"

    sku = Column(String(64), primary_key=True)
    name = Column(String(200), nullable=False)
    aliases = Column(Text)                       # otros nombres universales, separados por "|"
    description = Column(Text)
    category = Column(String(100))
    unit = Column(String(30), nullable=False)
    supplier = Column(String(200))
    supplier_cost = Column(Numeric(14, 2))
    retail_price = Column(Numeric(14, 2))
    wholesale_price = Column(Numeric(14, 2))
    is_active = Column(Boolean, nullable=False, default=True)
    catalog_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=schemas_utils.current_utc_time)
