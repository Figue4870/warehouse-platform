import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app.cache import InventoryCache, MISS, get_inventory_cache
from app.config import settings
from app.events import types as events
from app.events.outbox import OutboxWriter
from app.events.payloads import alert_payload, movement_payload
from app.modules.alerts.alerts_service import DISCREPANCY, OUT_OF_STOCK, AlertService
from app.modules.inventory.inventory_repository import InventoryRepository
from app.modules.inventory.inventory_rules import calculate_new_balance, lot_delta
from app.modules.inventory.models import InventoryEntity, InventoryMovementEntity
from app.modules.products.models import ProductEntity
from app.modules.inventory.schemas import (
    InventoryResponse,
    MovementCreate,
    MovementResponse,
    MovementResult,
    MovementType,
    TransactionHistory,
    TransactionRow,
)
from app.modules.shared.errors import BusinessRuleError

logger = logging.getLogger("warehouse.inventory")

# Fase 6: solo la Saga (command-worker) puede registrar estos tipos
TRANSFER_TYPES = {MovementType.TRANSFER_IN, MovementType.TRANSFER_OUT}
PURCHASE, SALE = MovementType.RECEIPT.value, MovementType.SHIPMENT.value


def default_unit_price(m_type: MovementType, product: ProductEntity):
    """Compras al costo del proveedor; ventas al precio de tienda."""
    if m_type == MovementType.RECEIPT:
        return product.supplier_cost
    if m_type == MovementType.SHIPMENT:
        return product.retail_price
    return None


@dataclass
class AppliedMovement:
    movement: InventoryMovementEntity
    product: ProductEntity
    inventory: InventoryEntity
    alerts_generated: List[str] = field(default_factory=list)


class InventoryService:
    def __init__(self, db: Session, cache: InventoryCache | None = None):
        self.db = db
        self.repo = InventoryRepository(db)
        self.alerts = AlertService(db)
        self.outbox = OutboxWriter(db)
        self.cache = cache if cache is not None else get_inventory_cache()

    # Métodos que usa ProductService
    def initialize_stock(self, product_id: int) -> None:
        """Crea el saldo en 0. No hace commit: participa en la transacción de quien llama."""
        self.repo.initialize_inventory(product_id)

    def lock_inventory(self, product_id: int) -> InventoryEntity:
        """Bloquea la fila de inventario (FOR UPDATE). No hace commit."""
        row = self.repo.get_by_product_for_update(product_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventario no encontrado")
        return row[0]

    def invalidate_cache(self, product_id: int) -> None:
        # Estrategia A (síncrona). Con CACHE_INVALIDATION_MODE=async la hace solo el cache-worker.
        if settings.CACHE_INVALIDATION_MODE in ("sync", "both"):
            self.cache.invalidate(product_id)

    # GET /inventory/{product_id}: cache-aside (Fase 2)
    def get_stock(self, product_id: int) -> Tuple[dict, str]:
        """Devuelve (inventario, estado_cache) donde estado_cache es HIT, MISS o BYPASS."""
        # 1) Primero preguntar a Redis
        lookup = self.cache.get(product_id)
        if lookup.value is not None:
            return lookup.value, lookup.status  # HIT: PostgreSQL ni se entera

        # 2) MISS (o Redis apagado/caído): ir a la fuente de verdad
        inventory = self.repo.get_by_product(product_id)
        if not inventory:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventario no encontrado")
        data = InventoryResponse.model_validate(inventory).model_dump(mode="json")
        # Fase 11: termina la transacción de lectura y devuelve la conexión al pool ya, no al cerrar la petición
        self.db.rollback()

        # 3) Guardar en Redis con TTL para las próximas lecturas
        if lookup.status == MISS:
            self.cache.set(product_id, data)
        return data, lookup.status

    def list_stock(self, limit: int) -> List[dict]:
        """Saldo de todos los productos activos (lectura directa de PostgreSQL)."""
        return [{
            "product_id": product.id, "sku": product.sku, "name": product.name, "category": product.category,
            "unit": product.unit, "lot": product.lot, "quantity": inventory.quantity,
            "reserved_quantity": inventory.reserved_quantity, "retail_price": product.retail_price,
            "updated_at": inventory.updated_at,
        } for inventory, product in self.repo.list_stock(limit)]

    def get_stock_by_sku(self, sku: str) -> dict:
        """Fase 7: la central consulta una bodega por SKU (el id local cambia entre bodegas)."""
        row = self.repo.get_by_sku(sku)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"El SKU {sku} no existe en {settings.WAREHOUSE_ID}")
        inventory, product = row
        return {
            "warehouse_id": settings.WAREHOUSE_ID,
            "sku": product.sku,
            "product_id": product.id,
            "is_active": product.is_active,
            "quantity": inventory.quantity,
            "reserved_quantity": inventory.reserved_quantity,
            "version": inventory.version,
            "updated_at": inventory.updated_at.isoformat() if inventory.updated_at else None,
        }

    # Núcleo de un movimiento, sin commit (lo usan la API y la Saga)
    def apply_movement(
        self,
        product_id: int,
        m_type: MovementType,
        quantity: int,
        lot: str | None = None,
        reference: str | None = None,
        from_reservation: bool = False,
        unit_price=None,
        counterparty: str | None = None,
        actor: str | None = None,
        alert_on_adjustment: bool = True,
    ) -> AppliedMovement:
        """Aplica un movimiento sin hacer commit (lo usan la API, las solicitudes y el command-worker)."""
        m_type = MovementType(m_type)
        alerts_generated: List[str] = []

        # 1. Leer el saldo actual bloqueando la fila
        row = self.repo.get_by_product_for_update(product_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No existe inventario para el producto {product_id} en la bodega {settings.WAREHOUSE_ID}",
            )
        inventory, product = row
        if not product.is_active:
            raise BusinessRuleError("El producto está dado de baja; no admite movimientos")

        before_qty = inventory.quantity
        before_reserved = inventory.reserved_quantity

        # 2. Aplicar la regla de negocio (lanza BusinessRuleError si quedaría negativo)
        new_qty, new_reserved = calculate_new_balance(
            m_type, quantity, before_qty, before_reserved, from_reservation=from_reservation
        )

        # 3. Verificar y actualizar el lote (si se indicó)
        if lot:
            self._apply_lot(product_id, lot, lot_delta(m_type, quantity))

        # 4. Guardar nuevo saldo + historial
        self.repo.update_stock(inventory, new_qty, new_reserved)
        movement = self.repo.register_movement(
            product_id=product_id,
            m_type=m_type.value,
            qty=quantity,
            quantity_after=new_qty,
            reserved_after=new_reserved,
            lot=lot,
            ref=reference,
            unit_price=unit_price if unit_price is not None else default_unit_price(m_type, product),
            counterparty=counterparty,
            created_by=actor,
        )

        # 5. Evento del movimiento para RabbitMQ (Fase 3) — misma transacción
        self.outbox.add(events.INVENTORY_MOVEMENT_RECORDED, "inventory", product.sku,
                        movement_payload(movement, product, inventory))

        # 6. Logs automáticos que pide el PDF (misma transacción) + su evento
        if before_qty > 0 and new_qty == 0:
            alert = self.alerts.register(
                product_id,
                OUT_OF_STOCK,
                f"Producto {product_id} agotado en {settings.WAREHOUSE_ID} tras {m_type.value} "
                f"de {quantity} (reservado: {new_reserved})",
                movement.id,
            )
            self.outbox.add(events.STOCK_DEPLETED, "alert", alert.id, alert_payload(alert, product))
            alerts_generated.append(OUT_OF_STOCK)
        if m_type == MovementType.ADJUSTMENT and alert_on_adjustment:
            alert = self.alerts.register(
                product_id,
                DISCREPANCY,
                f"Desfase de inventario en producto {product_id}: el sistema tenía {before_qty}, "
                f"se ajustó {quantity:+d} (ahora {new_qty}). "
                f"Motivo: {reference or 'sin referencia'}",
                movement.id,
            )
            self.outbox.add(events.STOCK_DISCREPANCY, "alert", alert.id, alert_payload(alert, product))
            alerts_generated.append(DISCREPANCY)

        return AppliedMovement(movement, product, inventory, alerts_generated)

    def _apply_lot(self, product_id: int, lot: str, delta: int) -> None:
        if delta == 0:
            return
        row = self.repo.get_lot_for_update(product_id, lot)
        if row is None:
            if delta < 0:
                raise BusinessRuleError(f"El lote {lot} no existe para este producto en {settings.WAREHOUSE_ID}")
            self.repo.add_lot(product_id, lot, delta)
            return
        if row.quantity + delta < 0:
            raise BusinessRuleError(f"El lote {lot} no alcanza. Disponible en el lote: {row.quantity}, requerido: {-delta}")
        row.quantity += delta

    # POST /inventory/movements: una sola transacción (Fase 1)
    def process_movement(self, movement_in: MovementCreate, actor: str | None = None) -> MovementResult:
        """Registra un movimiento en una sola transacción (todo o nada)."""
        product_id = movement_in.product_id
        m_type = MovementType(movement_in.type)
        if m_type in TRANSFER_TYPES:
            # Fase 6: una transferencia "a mano" (salida aquí sin la entrada allá) descuadraría
            # el inventario de la empresa. Solo la Saga de la central registra estos movimientos.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Las transferencias entre bodegas se piden en la central: POST /transfers (Fase 6, Saga). "
                       "La Saga registra TRANSFER_OUT y TRANSFER_IN en las dos bodegas.",
            )
        try:
            applied = self.apply_movement(product_id, m_type, movement_in.quantity, movement_in.lot, movement_in.reference,
                                          unit_price=movement_in.unit_price, counterparty=movement_in.counterparty,
                                          actor=actor)
            movement, alerts_generated = applied.movement, applied.alerts_generated
            new_qty, new_reserved = movement.quantity_after, movement.reserved_after

            # 6. Confirmar TODO junto
            self.db.commit()

        except BusinessRuleError as error:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.message)
        except HTTPException:
            self.db.rollback()
            raise
        except IntegrityError:
            # Ej.: el CHECK (quantity >= 0) de PostgreSQL rechazó el cambio
            self.db.rollback()
            logger.exception("La base de datos rechazó el movimiento", extra={"event": "movement_integrity_error"})
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="La base de datos rechazó la operación")
        except (OperationalError, PoolTimeoutError):
            # PostgreSQL caído o sin conexiones libres: main.py lo convierte en HTTP 503
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            logger.exception("Error inesperado procesando movimiento", extra={"event": "movement_error"})
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno procesando el movimiento")

        # 7. Invalidación síncrona del caché (Estrategia A) — SIEMPRE después del COMMIT
        self.invalidate_cache(product_id)

        self.db.refresh(movement)
        context = {
            "product_id": product_id,
            "movement_id": movement.id,
            "type": m_type.value,
            "quantity": movement_in.quantity,
            "quantity_after": new_qty,
            "reserved_after": new_reserved,
        }
        logger.info("Movimiento registrado", extra={"event": "inventory_movement_registered", "context": context})
        if OUT_OF_STOCK in alerts_generated:
            logger.warning("Producto agotado", extra={"event": "out_of_stock", "context": context})
        if DISCREPANCY in alerts_generated:
            logger.warning("Desfase de inventario registrado", extra={"event": "inventory_discrepancy", "context": context})

        result = MovementResponse.model_validate(movement).model_dump()
        return MovementResult(**result, alerts_generated=alerts_generated)

    def get_lots(self, product_id: int):
        if self.repo.get_by_product(product_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventario no encontrado")
        return self.repo.get_lots(product_id)

    def get_transactions(self, date_from: Optional[date], date_to: Optional[date], limit: int) -> TransactionHistory:
        """Historial de compras y ventas para reportes (el total usa el precio guardado en cada movimiento)."""
        start = datetime.combine(date_from, time.min, tzinfo=timezone.utc) if date_from else None
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc) if date_to else None
        rows, totals = [], {PURCHASE: [0, Decimal("0")], SALE: [0, Decimal("0")]}
        for movement, product in self.repo.get_transactions((PURCHASE, SALE), start, end, limit):
            total = movement.unit_price * movement.quantity if movement.unit_price is not None else None
            totals[movement.type][0] += movement.quantity
            totals[movement.type][1] += total or 0
            base = MovementResponse.model_validate(movement).model_dump()
            rows.append(TransactionRow(**base, sku=product.sku, product_name=product.name, total=total))
        return TransactionHistory(purchases_units=totals[PURCHASE][0], purchases_total=totals[PURCHASE][1],
                                  sales_units=totals[SALE][0], sales_total=totals[SALE][1], rows=rows)

    def get_movements(self, product_id: int, limit: int, offset: int):
        if self.repo.get_by_product(product_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventario no encontrado")
        return self.repo.get_movements_by_product(product_id, limit, offset)
