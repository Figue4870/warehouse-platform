from decimal import Decimal
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.cache import InventoryCache, get_inventory_cache
from app.database import get_db
from app.modules.catalog.catalog_service import CatalogService
from app.modules.products.schemas import ProductResponse
from app.security import ANY_USER, WAREHOUSE_ADMINS, require

router = APIRouter(prefix="/catalog", tags=["Catálogo"])

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class LocalCatalogEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sku: str = Field(..., min_length=1, max_length=64)
    lot: Optional[str] = Field(None, max_length=100)
    supplier: Optional[str] = Field(None, max_length=200)
    supplier_cost: Optional[Money] = None
    retail_price: Optional[Money] = None
    wholesale_price: Optional[Money] = None


@router.get("/universal", summary="Catálogo universal (lo define el administrador global en la central)")
def list_universal(search: Optional[str] = Query(None, max_length=100), limit: int = Query(200, ge=1, le=1000),
                   db: Session = Depends(get_db), _=Depends(require(*ANY_USER))):
    return CatalogService(db).list(search, limit)


@router.post("/local", response_model=ProductResponse, status_code=status.HTTP_201_CREATED,
             summary="Agregar un producto universal al catálogo de esta bodega")
def add_local(entry: LocalCatalogEntry, db: Session = Depends(get_db),
              cache: InventoryCache = Depends(get_inventory_cache), _=Depends(require(*WAREHOUSE_ADMINS))):
    overrides = entry.model_dump(exclude={"sku"})
    return CatalogService(db, cache).add_to_local_catalog(entry.sku, overrides)
