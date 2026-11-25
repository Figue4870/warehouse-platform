"""Catálogo universal: lo administra el administrador global y llega a todas las bodegas por comandos."""

import logging
from decimal import Decimal
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from platform_common.auth import ADMIN_GLOBAL, Principal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from central import platform_commands
from central.database import get_db
from central.models import UniversalProductEntity
from central.projection import now_utc
from central.security import ANY_USER, require

router = APIRouter(prefix="/catalog", tags=["Catálogo universal"])
logger = logging.getLogger("central.catalog")

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class UniversalProductIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    aliases: List[str] = Field(default_factory=list, max_length=10, description="Otros nombres universales")
    description: Optional[str] = Field(None, max_length=1000)
    category: Optional[str] = Field(None, max_length=100)
    unit: str = Field("UNIDAD", min_length=1, max_length=30)
    supplier: Optional[str] = Field(None, max_length=200)
    supplier_cost: Optional[Money] = None
    retail_price: Optional[Money] = None
    wholesale_price: Optional[Money] = None


class UniversalProductCreate(UniversalProductIn):
    sku: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


def view(product: UniversalProductEntity) -> dict:
    return {**platform_commands.catalog_payload(product), "created_by": product.created_by,
            "created_at": product.created_at, "updated_at": product.updated_at}


def create_universal_product(db: Session, data: UniversalProductCreate, principal: Principal) -> UniversalProductEntity:
    if db.get(UniversalProductEntity, data.sku) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"El SKU {data.sku} ya está en el catálogo")
    product = UniversalProductEntity(**data.model_dump(), catalog_version=1, is_active=True, created_by=principal.username,
                                     created_at=now_utc(), updated_at=now_utc())
    db.add(product)
    db.flush()
    sent = platform_commands.publish_catalog_product(db, product)
    logger.info("Producto universal creado", extra={"event": "universal_product_created", "context": {
        "sku": product.sku, "warehouses": sent, "by": principal.username}})
    return product


@router.get("", summary="Productos universales")
def list_catalog(search: Optional[str] = Query(None, max_length=100), include_inactive: bool = False,
                 db: Session = Depends(get_db), _: Principal = Depends(require(*ANY_USER))):
    query = db.query(UniversalProductEntity)
    if not include_inactive:
        query = query.filter(UniversalProductEntity.is_active.is_(True))
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(UniversalProductEntity.sku.ilike(pattern), UniversalProductEntity.name.ilike(pattern)))
    return [view(p) for p in query.order_by(UniversalProductEntity.name).all()]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Crear un producto universal (llega a todas las bodegas)")
def create_product(data: UniversalProductCreate, db: Session = Depends(get_db), principal: Principal = Depends(require(ADMIN_GLOBAL))):
    product = create_universal_product(db, data, principal)
    db.commit()
    return view(product)


@router.put("/{sku}", summary="Cambiar un producto universal (sube su versión y se reenvía a las bodegas)")
def update_product(sku: str, data: UniversalProductIn, db: Session = Depends(get_db),
                   principal: Principal = Depends(require(ADMIN_GLOBAL))):
    product = db.get(UniversalProductEntity, sku)
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    for field, value in data.model_dump().items():
        setattr(product, field, value)
    product.catalog_version += 1
    product.updated_at = now_utc()
    platform_commands.publish_catalog_product(db, product)
    db.commit()
    return view(product)


@router.delete("/{sku}", summary="Retirar un producto del catálogo universal")
def retire_product(sku: str, db: Session = Depends(get_db), principal: Principal = Depends(require(ADMIN_GLOBAL))):
    product = db.get(UniversalProductEntity, sku)
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    product.is_active = False
    product.catalog_version += 1
    product.updated_at = now_utc()
    platform_commands.publish_catalog_product(db, product)
    db.commit()
    return view(product)
