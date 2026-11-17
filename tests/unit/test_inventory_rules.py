"""Pruebas de la función pura calculate_new_balance (sin base de datos)."""

import pytest

from app.modules.inventory.inventory_rules import calculate_new_balance
from app.modules.inventory.schemas import MovementType as T
from app.modules.shared.errors import BusinessRuleError


def test_entrada_suma_al_disponible():
    assert calculate_new_balance(T.RECEIPT, 5, available=10, reserved=0) == (15, 0)


def test_ejemplo_del_readme_salida_de_4_sobre_10_deja_6():
    assert calculate_new_balance(T.SHIPMENT, 4, available=10, reserved=0) == (6, 0)


def test_ejemplo_del_readme_salida_de_15_sobre_10_se_rechaza():
    with pytest.raises(BusinessRuleError, match="Stock insuficiente"):
        calculate_new_balance(T.SHIPMENT, 15, available=10, reserved=0)


def test_las_unidades_reservadas_no_se_pueden_vender():
    # 3 disponibles + 7 reservadas: vender 5 debe fallar
    with pytest.raises(BusinessRuleError):
        calculate_new_balance(T.SHIPMENT, 5, available=3, reserved=7)


def test_reserva_pasa_de_disponible_a_reservado():
    assert calculate_new_balance(T.RESERVATION, 4, available=10, reserved=1) == (6, 5)
