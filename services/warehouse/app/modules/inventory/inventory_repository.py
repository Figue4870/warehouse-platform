from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.modules.inventory.models import InventoryEntity, InventoryLotEntity, InventoryMovementEntity
from app.modules.products.models import ProductEntity


class InventoryRepository:
    """Acceso a las tablas inventory e inventory_movements. No hace commit."""

    def __init__(self, db: Session):
        self.db = db

    def initialize_inventory(self, product_id: int) -> InventoryEntity:
        inventory = InventoryEntity(
            product_id=product_id,
            warehouse_id=settings.WAREHOUSE_ID,
            quantity=0,
            reserved_quantity=0,
        )
        self.db.add(inventory)
        return inventory

    def get_by_product(self, product_id: int) -> Optional[InventoryEntity]:
        """Lectura normal (sin bloqueo). La usa GET /inventory/{id}."""
        return (
            self.db.query(InventoryEntity)
            .filter(
                InventoryEntity.product_id == product_id,
                InventoryEntity.warehouse_id == settings.WAREHOUSE_ID,
            )
            .first()
        )

    def list_stock(self, limit: int) -> List[Tuple[InventoryEntity, ProductEntity]]:
        return (
            self.db.query(InventoryEntity, ProductEntity)
            .join(ProductEntity, ProductEntity.id == InventoryEntity.product_id)
            .filter(InventoryEntity.warehouse_id == settings.WAREHOUSE_ID, ProductEntity.is_active.is_(True))
            .order_by(ProductEntity.name)
            .limit(limit)
            .all()
        )

    def get_by_sku(self, sku: str) -> Optional[Tuple[InventoryEntity, ProductEntity]]:
        row = (
            self.db.query(InventoryEntity, ProductEntity)
            .join(ProductEntity, ProductEntity.id == InventoryEntity.product_id)
            .filter(ProductEntity.sku == sku, InventoryEntity.warehouse_id == settings.WAREHOUSE_ID)
            .first()
        )
        return (row[0], row[1]) if row else None

    def get_by_product_for_update(self, product_id: int) -> Optional[Tuple[InventoryEntity, ProductEntity]]:
        """Lee el saldo con bloqueo de fila (SELECT ... FOR UPDATE)."""
        row = (
            self.db.query(InventoryEntity, ProductEntity)
            .join(ProductEntity, ProductEntity.id == InventoryEntity.product_id)
            .filter(
                InventoryEntity.product_id == product_id,
                InventoryEntity.warehouse_id == settings.WAREHOUSE_ID,
            )
            .with_for_update()
            .first()
        )
        if row is None:
            return None
        return row[0], row[1]

    def update_stock(self, inventory: InventoryEntity, quantity: int, reserved_quantity: int) -> None:
        inventory.quantity = quantity
        inventory.reserved_quantity = reserved_quantity
        inventory.version = (inventory.version or 0) + 1  # Fase 3: versión para sincronizar
        self.db.add(inventory)

    def register_movement(
        self,
        product_id: int,
        m_type: str,
        qty: int,
        quantity_after: int,
        reserved_after: int,
        lot: Optional[str],
        ref: Optional[str],
        unit_price=None,
        counterparty: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> InventoryMovementEntity:
        movement = InventoryMovementEntity(
            product_id=product_id,
            warehouse_id=settings.WAREHOUSE_ID,
            type=m_type,
            quantity=qty,
            quantity_after=quantity_after,
            reserved_after=reserved_after,
            lot=lot,
            reference=ref,
            unit_price=unit_price,
            counterparty=counterparty,
            created_by=created_by,
        )
        self.db.add(movement)
        self.db.flush()  # para conocer movement.id (lo usan las alertas)
        return movement

    def get_lot_for_update(self, product_id: int, lot: str) -> Optional[InventoryLotEntity]:
        return (
            self.db.query(InventoryLotEntity)
            .filter(InventoryLotEntity.product_id == product_id, InventoryLotEntity.warehouse_id == settings.WAREHOUSE_ID,
                    InventoryLotEntity.lot == lot)
            .with_for_update()
            .first()
        )

    def add_lot(self, product_id: int, lot: str, quantity: int) -> InventoryLotEntity:
        row = InventoryLotEntity(product_id=product_id, warehouse_id=settings.WAREHOUSE_ID, lot=lot, quantity=quantity)
        self.db.add(row)
        return row

    def get_lots(self, product_id: int) -> List[InventoryLotEntity]:
        return (
            self.db.query(InventoryLotEntity)
            .filter(InventoryLotEntity.product_id == product_id, InventoryLotEntity.warehouse_id == settings.WAREHOUSE_ID)
            .order_by(InventoryLotEntity.lot)
            .all()
        )

    def latest_movement(self, product_id: int) -> Optional[InventoryMovementEntity]:
        return (
            self.db.query(InventoryMovementEntity)
            .filter(InventoryMovementEntity.product_id == product_id,
                    InventoryMovementEntity.warehouse_id == settings.WAREHOUSE_ID)
            .order_by(InventoryMovementEntity.id.desc())
            .first()
        )

    def get_transactions(self, types, date_from, date_to, limit: int):
        query = (
            self.db.query(InventoryMovementEntity, ProductEntity)
            .join(ProductEntity, ProductEntity.id == InventoryMovementEntity.product_id)
            .filter(InventoryMovementEntity.warehouse_id == settings.WAREHOUSE_ID, InventoryMovementEntity.type.in_(types))
        )
        if date_from is not None:
            query = query.filter(InventoryMovementEntity.created_at >= date_from)
        if date_to is not None:
            query = query.filter(InventoryMovementEntity.created_at < date_to)
        return query.order_by(InventoryMovementEntity.id.desc()).limit(limit).all()

    def get_movements_by_product(self, product_id: int, limit: int, offset: int) -> List[InventoryMovementEntity]:
        return (
            self.db.query(InventoryMovementEntity)
            .filter(
                InventoryMovementEntity.product_id == product_id,
                InventoryMovementEntity.warehouse_id == settings.WAREHOUSE_ID,
            )
            .order_by(InventoryMovementEntity.created_at.desc(), InventoryMovementEntity.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
