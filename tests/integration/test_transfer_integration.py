import os
import time
import uuid

import httpx
import pytest


def eventually(fetch, predicate):
    for _ in range(90):
        value = fetch()
        if predicate(value):
            return value
        time.sleep(1)
    pytest.fail(f"Se agotó la espera. Última respuesta: {value}")


def test_transferencia_real_conserva_inventario(api):
    destination_url = os.getenv("SECOND_WAREHOUSE_URL")
    if not destination_url:
        pytest.skip("SECOND_WAREHOUSE_URL requiere una segunda bodega en ejecución")
    sku = f"IT-TR-{uuid.uuid4().hex[:12]}"

    def prepare(client, stock):
        product = client.post("/products", json={"sku": sku, "name": "Transferencia", "unit": "U"})
        product.raise_for_status()
        product_id = product.json()["id"]
        client.post("/inventory/movements", json={
            "product_id": product_id, "quantity": stock, "type": "RECEIPT",
        }).raise_for_status()
        return product_id

    with httpx.Client(base_url=destination_url, timeout=10) as dest, httpx.Client(
        base_url=os.getenv("CENTRAL_URL", "http://localhost:8090"), timeout=10,
    ) as central:
        source_id = prepare(api, 100)
        dest_id = prepare(dest, 20)
        response = eventually(
            lambda: central.post("/transfers", json={
                "sku": sku, "source_warehouse_id": "WH-001", "destination_warehouse_id": "WH-002", "quantity": 30,
            }),
            lambda result: result.status_code != 404,
        )
        assert response.status_code == 202, response.text
        state = eventually(lambda: central.get(response.headers["Location"]).raise_for_status().json(),
                           lambda result: result["status"] != "PROCESSING")
        assert state["state"] == "COMPLETED", state
        source = api.get(f"/inventory/{source_id}").raise_for_status().json()
        destination = dest.get(f"/inventory/{dest_id}").raise_for_status().json()
        assert (source["quantity"], source["reserved_quantity"], destination["quantity"]) == (70, 0, 50)
