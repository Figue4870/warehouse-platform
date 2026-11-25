import logging
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.cache import InventoryCache
from app.modules.catalog.models import UniversalProductEntity
from app.modules.products.models import ProductEntity
from app.modules.products.products_service import ProductService
from app.modules.products.schemas import ProductCreate
from app.modules.shared.utils import schemas_utils

logger = logging.getLogger("warehouse.catalog")

APPLIED, STALE = "APPLIED", "STALE"


def _money(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


class CatalogService:
    def __init__(self, db: Session, cache: InventoryCache | None = None):
        self.db = db
        self.cache = cache

    def upsert(self, data: dict) -> str:
        """Aplica un producto del catálogo universal. Una versión vieja o repetida no cambia nada."""
        version = int(data.get("catalog_version") or 1)
        row = self.db.get(UniversalProductEntity, data["sku"])
        if row is not None and row.catalog_version >= version:
            return STALE
        if row is None:
            row = UniversalProductEntity(sku=data["sku"])
            self.db.add(row)
        row.name = data["name"]
        row.aliases = "|".join(data.get("aliases") or [])
        row.description = data.get("description")
        row.category = data.get("category")
        row.unit = data.get("unit") or "UNIDAD"
        row.supplier = data.get("supplier")
        row.supplier_cost = _money(data.get("supplier_cost"))
        row.retail_price = _money(data.get("retail_price"))
        row.wholesale_price = _money(data.get("wholesale_price"))
        row.is_active = bool(data.get("is_active", True))
        row.catalog_version = version
        row.updated_at = schemas_utils.current_utc_time()
        self.db.flush()
        return APPLIED

    def get(self, sku: str) -> Optional[UniversalProductEntity]:
        return self.db.get(UniversalProductEntity, sku)

    def list(self, search: Optional[str], limit: int) -> List[dict]:
        query = self.db.query(UniversalProductEntity).filter(UniversalProductEntity.is_active.is_(True))
        if search:
            pattern = f"%{search.strip()}%"
            query = query.filter(or_(UniversalProductEntity.sku.ilike(pattern), UniversalProductEntity.name.ilike(pattern),
                                     UniversalProductEntity.aliases.ilike(pattern)))
        rows = query.order_by(UniversalProductEntity.name).limit(limit).all()
        local = {sku: pid for sku, pid in self.db.query(ProductEntity.sku, ProductEntity.id)
                 .filter(ProductEntity.sku.in_([r.sku for r in rows]), ProductEntity.is_active.is_(True))}
        return [{
            "sku": r.sku, "name": r.name, "aliases": [a for a in (r.aliases or "").split("|") if a],
            "description": r.description, "category": r.category, "unit": r.unit, "supplier": r.supplier,
            "supplier_cost": r.supplier_cost, "retail_price": r.retail_price, "wholesale_price": r.wholesale_price,
            "catalog_version": r.catalog_version, "local_product_id": local.get(r.sku),
        } for r in rows]

    def add_to_local_catalog(self, sku: str, overrides: dict) -> ProductEntity:
        """El administrador de bodega incorpora un producto universal a su catálogo (con precios y lote propios)."""
        universal = self.get(sku)
        if universal is None or not universal.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El SKU {sku} no está en el catálogo universal")
        data = {
            "sku": universal.sku, "name": universal.name, "description": universal.description,
            "category": universal.category, "unit": universal.unit, "supplier": universal.supplier,
            "supplier_cost": universal.supplier_cost, "retail_price": universal.retail_price,
            "wholesale_price": universal.wholesale_price,
        }
        data.update({k: v for k, v in overrides.items() if v is not None})
        return ProductService(self.db, self.cache).create_product_with_inventory(ProductCreate(**data))
