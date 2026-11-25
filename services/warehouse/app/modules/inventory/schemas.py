from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class MovementType(str, Enum):
    RECEIPT = "RECEIPT"
    SHIPMENT = "SHIPMENT"
    ADJUSTMENT = "ADJUSTMENT"
    RESERVATION = "RESERVATION"
    RELEASE = "RELEASE"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    RETURN = "RETURN"


class MovementCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    product_id: int = Field(..., gt=0)
    type: MovementType = Field(..., description="RECEIPT, SHIPMENT, ADJUSTMENT, RESERVATION, RELEASE, TRANSFER_OUT, TRANSFER_IN, RETURN")
    quantity: int = Field(
        ...,
        description="Unidades. Siempre > 0, excepto en ADJUSTMENT: positivo suma, negativo resta.",
    )
    lot: Optional[str] = Field(None, max_length=100)
    reference: Optional[str] = Field(None, max_length=200, description="Factura, guía, motivo del ajuste...")
    unit_price: Optional[Money] = Field(None, description="Precio unitario. Por defecto: costo del proveedor en entradas y precio de tienda en salidas")
    counterparty: Optional[str] = Field(None, max_length=200, description="Proveedor (entrada) o cliente (salida)")

    @model_validator(mode="after")
    def validate_quantity(self):
        if self.type == MovementType.ADJUSTMENT:
            if self.quantity == 0:
                raise ValueError("En un ADJUSTMENT la cantidad no puede ser 0 (positivo suma, negativo resta)")
        elif self.quantity <= 0:
            raise ValueError("La cantidad debe ser mayor que 0")
        return self


class MovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    warehouse_id: str
    type: str
    quantity: int
    quantity_after: int
    reserved_after: int
    lot: Optional[str]
    reference: Optional[str]
    unit_price: Optional[Decimal] = None
    counterparty: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime


class TransactionRow(MovementResponse):
    sku: str
    product_name: str
    total: Optional[Decimal] = None


class TransactionHistory(BaseModel):
    """Compras (RECEIPT) y ventas (SHIPMENT) con sus totales."""

    purchases_units: int
    purchases_total: Decimal
    sales_units: int
    sales_total: Decimal
    rows: List[TransactionRow]


class LotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lot: str
    quantity: int
    updated_at: datetime


class MovementResult(MovementResponse):
    """Respuesta de POST /inventory/movements: el movimiento + alertas que generó."""

    alerts_generated: List[str] = []


class InventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    warehouse_id: str
    quantity: int
    reserved_quantity: int
    version: int = 0
    updated_at: datetime
