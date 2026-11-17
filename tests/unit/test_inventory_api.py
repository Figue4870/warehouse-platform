"""Pruebas de movimientos de inventario: reglas, transacción y alertas (Fase 1)."""

from app.modules.alerts.alerts_service import AlertService


def stock(client, product_id):
    return client.get(f"/inventory/{product_id}").json()


def test_flujo_del_readme_10_menos_4_es_6_y_15_se_rechaza(client, create_product, move):
    product = create_product()
    assert move(product["id"], "RECEIPT", 10).status_code == 201
    response = move(product["id"], "SHIPMENT", 4)
    assert response.status_code == 201
    assert response.json()["quantity_after"] == 6
    assert stock(client, product["id"])["quantity"] == 6

    rejected = move(product["id"], "SHIPMENT", 15)
    assert rejected.status_code == 409
    assert "Stock insuficiente" in rejected.json()["detail"]
    # El rechazo no cambió nada: ni saldo ni historial
    assert stock(client, product["id"])["quantity"] == 6
    assert len(client.get(f"/inventory/{product['id']}/movements").json()) == 2


def test_rollback_si_algo_falla_a_mitad_de_la_transaccion(client, create_product, move, monkeypatch):
    """Si algo falla después de actualizar el saldo, se deshace todo."""
    product = create_product()
    move(product["id"], "RECEIPT", 5)

    def explode(*args, **kwargs):
        raise RuntimeError("fallo simulado en medio de la transacción")

    monkeypatch.setattr(AlertService, "register", explode)
    response = move(product["id"], "SHIPMENT", 5)  # dejaría el stock en 0 -> intenta crear alerta
    assert response.status_code == 500
    assert response.json()["detail"] == "Error interno procesando el movimiento"  # sin detalles internos

    assert stock(client, product["id"])["quantity"] == 5  # ROLLBACK: el saldo sigue igual
    assert len(client.get(f"/inventory/{product['id']}/movements").json()) == 1  # solo la entrada


def test_alerta_automatica_cuando_se_agota(client, create_product, move):
    product = create_product()
    move(product["id"], "RECEIPT", 2)
    response = move(product["id"], "SHIPMENT", 2)
    assert response.json()["alerts_generated"] == ["OUT_OF_STOCK"]
    alerts = client.get("/alerts", params={"type": "OUT_OF_STOCK"}).json()
    assert len(alerts) == 1
    assert alerts[0]["product_id"] == product["id"]
    assert alerts[0]["movement_id"] == response.json()["id"]


def test_reserva_y_liberacion(client, create_product, move):
    product = create_product()
    move(product["id"], "RECEIPT", 10)
    move(product["id"], "RESERVATION", 4)
    assert stock(client, product["id"])["quantity"] == 6
    assert stock(client, product["id"])["reserved_quantity"] == 4
    assert move(product["id"], "RELEASE", 5).status_code == 409
    move(product["id"], "RELEASE", 4)
    assert stock(client, product["id"])["quantity"] == 10
