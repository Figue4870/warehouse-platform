"""Reglas de negocio del inventario como funciones puras."""

from typing import Tuple

from app.modules.inventory.schemas import MovementType
from app.modules.shared.errors import BusinessRuleError

INCREASES_AVAILABLE = {MovementType.RECEIPT, MovementType.TRANSFER_IN, MovementType.RETURN}
DECREASES_AVAILABLE = {MovementType.SHIPMENT, MovementType.TRANSFER_OUT}


def calculate_new_balance(
    movement_type: MovementType, quantity: int, available: int, reserved: int
) -> Tuple[int, int]:
    """Calcula el nuevo saldo (disponible, reservado) después de un movimiento."""
    movement_type = MovementType(movement_type)

    if movement_type in INCREASES_AVAILABLE:
        return available + quantity, reserved

    if movement_type in DECREASES_AVAILABLE:
        if quantity > available:
            raise BusinessRuleError(f"Stock insuficiente. Disponible: {available}, requerido: {quantity}")
        return available - quantity, reserved

    if movement_type == MovementType.RESERVATION:
        if quantity > available:
            raise BusinessRuleError(f"Stock insuficiente para reservar. Disponible: {available}, requerido: {quantity}")
        return available - quantity, reserved + quantity

    if movement_type == MovementType.RELEASE:
        if quantity > reserved:
            raise BusinessRuleError(f"La liberación excede lo reservado. Reservado: {reserved}, solicitado: {quantity}")
        return available + quantity, reserved - quantity

    if movement_type == MovementType.ADJUSTMENT:
        # quantity con signo: +5 = aparecieron 5 unidades, -3 = faltan 3 unidades
        new_available = available + quantity
        if new_available < 0:
            raise BusinessRuleError(
                f"El ajuste dejaría el inventario en negativo. Disponible: {available}, ajuste: {quantity}"
            )
        return new_available, reserved

    raise BusinessRuleError(f"Tipo de movimiento no soportado: {movement_type}")
