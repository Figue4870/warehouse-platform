"""Fase 11: con la base saturada la API responde 503 rápido en vez de encolar peticiones."""

import inspect

from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.database import get_db
from app.modules.inventory.inventory_service import InventoryService


def test_sin_conexiones_libres_responde_503_con_retry_after(client, create_product, monkeypatch):
    product = create_product()

    def saturated(*args, **kwargs):
        raise PoolTimeoutError("QueuePool limit reached")

    monkeypatch.setattr(InventoryService, "apply_movement", saturated)
    response = client.post("/inventory/movements", json={"product_id": product["id"], "type": "RECEIPT", "quantity": 1})
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"


def test_la_sesion_se_cierra_en_el_event_loop():
    # Si fuera un generador síncrono, el cierre esperaría un hilo libre y bajo carga bloquearía el pool
    assert inspect.isasyncgenfunction(get_db)


def test_la_consulta_de_saldo_libera_la_conexion_antes_de_responder(session_factory, cache, create_product, move):
    # La respuesta se serializa en otro hilo; si la transacción siguiera abierta, la conexión quedaría tomada
    product = create_product()
    move(product["id"], "RECEIPT", 3)
    db = session_factory()
    try:
        data, _ = InventoryService(db, cache).get_stock(product["id"])
        assert data["quantity"] == 3 and not db.in_transaction()
    finally:
        db.close()
