"""Fase 6 contra la plataforma real: transferencia WH-001 -> WH-002 orquestada por la central."""

from it_helpers import local_product, universal_sku, wait_until


def test_transferencia_completa_entre_dos_bodegas(api, wh2, central):
    wh1 = api
    sku = universal_sku(central, "IT-TR")
    local_product(wh1, sku, initial_stock=10)
    # La central debe conocer el SKU (para que WH-002 pueda crear el producto, que aún no tiene)
    wait_until(lambda: central.get(f"/global/inventory/{sku}").status_code == 200, what="el SKU en la central")

    response = central.post("/transfers", json={"sku": sku, "source_warehouse_id": "WH-001",
                                                "destination_warehouse_id": "WH-002", "quantity": 4})
    assert response.status_code == 202 and response.json()["status"] == "PROCESSING"
    transfer_id = response.json()["transfer_id"]

    done = wait_until(lambda: (t := central.get(f"/transfers/{transfer_id}").json())["state"] in ("COMPLETED", "FAILED",
                      "RESERVATION_RELEASED") and t, timeout=120, what="que la Saga termine")
    assert done["state"] == "COMPLETED", done["note"]
    assert wh1.get(f"/inventory/by-sku/{sku}").json()["quantity"] == 6
    assert wh2.get(f"/inventory/by-sku/{sku}").json()["quantity"] == 4    # WH-002 creó el producto al recibirlo
