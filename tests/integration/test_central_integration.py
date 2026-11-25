"""Fase 7 contra la plataforma real: bodega WH-001 + central, cada una en su proyecto de Docker."""

from it_helpers import wait_until


def test_un_movimiento_en_la_bodega_llega_a_la_central(central, api, new_product):
    product = new_product(initial_stock=12)
    api.post("/inventory/movements", json={"product_id": product["id"], "type": "SHIPMENT", "quantity": 2})

    data = wait_until(lambda: (d := central.get(f"/global/inventory/{product['sku']}").json()).get("total_quantity") == 10 and d,
                      what="que el evento llegue al modelo de lectura")
    wh = next(w for w in data["warehouses"] if w["warehouse_id"] == "WH-001")
    assert wh["quantity"] == 10


def test_la_bodega_aparece_online_y_al_dia(central, api, new_product):
    new_product(initial_stock=1)

    def caught_up():
        warehouses = {w["warehouse_id"]: w for w in central.get("/warehouses").json()}
        wh = warehouses.get("WH-001")
        return wh and wh["status"] == "ONLINE" and wh["events_missing"] == 0 and wh["watermark_sequence"] == wh["reported_sequence"] and wh

    wh = wait_until(caught_up, what="que WH-001 quede ONLINE y sin eventos pendientes")
    assert wh["api_url"]
    assert wh["heartbeat"]["authenticated"] and wh["heartbeat"]["answered_by"].startswith("WH-001")
