"""Fase 5: cada respuesta dice qué instancia de la API la atendió."""



def test_la_api_no_guarda_estado_de_negocio_en_memoria(client, create_product, move):
    """Stateless: dos "instancias" que comparten la misma base de datos ven lo mismo."""
    product = create_product()
    move(product["id"], "RECEIPT", 5)
    assert client.get(f"/inventory/{product['id']}").json()["quantity"] == 5
    assert client.get(f"/products/{product['id']}").json()["sku"] == product["sku"]
