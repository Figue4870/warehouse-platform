"""Pruebas de los endpoints de productos (Fase 1)."""


def test_crear_producto_con_campos_del_pdf_e_inventario_en_cero(client):
    payload = {
        "sku": "EPP-CASCO-001",
        "name": "Casco de seguridad",
        "unit": "UNIDAD",
        "category": "EPP",
        "supplier": "Seguridad Industrial del Valle",
        "lot": "L-2026-10-A",
        "supplier_cost": "18000.00",
        "retail_price": 32000,
        "wholesale_price": "26000.5",
    }
    response = client.post("/products", json=payload)
    assert response.status_code == 201
    product = response.json()
    assert product["sku"] == "EPP-CASCO-001"
    assert product["supplier"] == "Seguridad Industrial del Valle"
    assert product["lot"] == "L-2026-10-A"
    assert product["retail_price"] == "32000.00"  # Decimal: llega como texto
    assert product["wholesale_price"] == "26000.50"
    assert product["is_active"] is True

    stock = client.get(f"/inventory/{product['id']}").json()
    assert stock["quantity"] == 0 and stock["reserved_quantity"] == 0
    assert stock["warehouse_id"] == "WH-TEST"


def test_sku_duplicado_devuelve_409(client, create_product):
    create_product(sku="DUP-1")
    response = client.post("/products", json={"sku": "DUP-1", "name": "Otro", "unit": "UNIDAD"})
    assert response.status_code == 409


def test_actualizar_producto_con_put(client, create_product):
    product = create_product()
    response = client.put(f"/products/{product['id']}", json={"name": "Nuevo nombre", "retail_price": "1500"})
    assert response.status_code == 200
    assert response.json()["name"] == "Nuevo nombre"
    assert response.json()["retail_price"] == "1500.00"
    assert response.json()["unit"] == "UNIDAD"  # lo que no se envía no cambia


def test_no_se_puede_dar_de_baja_con_existencias(client, create_product, move):
    product = create_product()
    move(product["id"], "RECEIPT", 3)
    response = client.delete(f"/products/{product['id']}")
    assert response.status_code == 409
    assert "existencias" in response.json()["detail"]
