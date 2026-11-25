from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from platform_common.auth import ADMIN_GLOBAL, LIDER, REGISTRADOR, Principal
from sqlalchemy.orm import Session

from app.cache import InventoryCache, get_inventory_cache
from app.database import get_db
from app.modules.inventory.inventory_service import InventoryService
from app.modules.inventory.schemas import (
    InventoryResponse,
    LotResponse,
    MovementCreate,
    MovementResponse,
    MovementResult,
    MovementType,
    TransactionHistory,
)
from app.security import ANY_USER, OPERATORS, require

router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("", summary="Saldo de todos los productos de la bodega", dependencies=[Depends(require(*ANY_USER))])
def list_inventory(limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db)):
    return InventoryService(db).list_stock(limit)


@router.get("/transactions", response_model=TransactionHistory, summary="Historial de compras (RECEIPT) y ventas (SHIPMENT)",
            dependencies=[Depends(require(*OPERATORS, LIDER, ADMIN_GLOBAL))])
def get_transactions(date_from: Optional[date] = Query(None, alias="from"), date_to: Optional[date] = Query(None, alias="to"),
                     limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db)):
    return InventoryService(db).get_transactions(date_from, date_to, limit)


@router.get("/by-sku/{sku}", summary="Saldo por SKU, leído en vivo de PostgreSQL (Fase 7: lo usa la consulta 'live' de la central)",
            dependencies=[Depends(require(*ANY_USER))])
def get_inventory_by_sku(sku: str, db: Session = Depends(get_db),
                         cache: InventoryCache = Depends(get_inventory_cache)):
    return InventoryService(db, cache).get_stock_by_sku(sku)


@router.get("/{product_id}", response_model=InventoryResponse,
            summary="Consultar saldo de un producto (usa caché Redis)", dependencies=[Depends(require(*ANY_USER))])
def get_inventory(product_id: int, response: Response, db: Session = Depends(get_db),
                  cache: InventoryCache = Depends(get_inventory_cache)):
    data, cache_status = InventoryService(db, cache).get_stock(product_id)
    # Cabecera para VER el caché en acción: HIT, MISS o BYPASS
    response.headers["X-Cache"] = cache_status
    return data


@router.post("/movements", response_model=MovementResult, status_code=status.HTTP_201_CREATED,
             summary="Registrar entrada, salida, ajuste, reserva...")
def create_movement(payload: MovementCreate, db: Session = Depends(get_db),
                    cache: InventoryCache = Depends(get_inventory_cache),
                    principal: Principal = Depends(require(*OPERATORS))):
    if payload.type == MovementType.ADJUSTMENT and principal.role == REGISTRADOR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Los ajustes los aplica el administrador de bodega; repórtalo con una solicitud de ajuste")
    # El controlador solo delega: reglas, transacción e invalidación viven en el servicio.
    return InventoryService(db, cache).process_movement(payload, actor=principal.username)


@router.get("/{product_id}/lots", response_model=List[LotResponse], summary="Unidades por lote",
            dependencies=[Depends(require(*ANY_USER))])
def get_lots(product_id: int, db: Session = Depends(get_db)):
    return InventoryService(db).get_lots(product_id)


@router.get("/{product_id}/movements", response_model=List[MovementResponse],
            summary="Historial de movimientos (más reciente primero)", dependencies=[Depends(require(*ANY_USER))])
def get_movements(
    product_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    cache: InventoryCache = Depends(get_inventory_cache),
):
    return InventoryService(db, cache).get_movements(product_id, limit, offset)
