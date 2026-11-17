"""Pruebas del caché Redis con Cache-Aside (Fase 2), usando un Redis falso."""



def test_primera_lectura_miss_segunda_hit(client, create_product, fake_redis):
    product = create_product()
    first = client.get(f"/inventory/{product['id']}")
    second = client.get(f"/inventory/{product['id']}")
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert first.json() == second.json()


def test_un_movimiento_invalida_el_cache_y_no_hay_dato_obsoleto(client, create_product, move):
    product = create_product()
    move(product["id"], "RECEIPT", 100)
    client.get(f"/inventory/{product['id']}")                       # MISS -> guarda 100
    assert client.get(f"/inventory/{product['id']}").headers["X-Cache"] == "HIT"

    move(product["id"], "SHIPMENT", 10)                              # BD = 90 y se borra la clave
    after = client.get(f"/inventory/{product['id']}")
    assert after.headers["X-Cache"] == "MISS"                        # tuvo que ir a la BD
    assert after.json()["quantity"] == 90                            # ¡no 100!


def test_con_redis_caido_la_api_sigue_respondiendo_desde_postgres(client, create_product, move, fake_redis):
    product = create_product()
    move(product["id"], "RECEIPT", 7)
    fake_redis.down = True

    response = client.get(f"/inventory/{product['id']}")
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "BYPASS"
    assert response.json()["quantity"] == 7

    # Escribir también funciona (la invalidación falla en silencio, el TTL la cubre)
    assert move(product["id"], "SHIPMENT", 2).status_code == 201
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["redis"] == "down"


def test_el_cache_ahorra_consultas_sql(client, create_product, cache):
    product = create_product()
    url = f"/inventory/{product['id']}"

    client.post("/metrics/reset")
    for _ in range(20):
        client.get(url)
    with_cache = client.get("/metrics").json()

    cache.enabled = False
    client.post("/metrics/reset")
    for _ in range(20):
        client.get(url)
    without_cache = client.get("/metrics").json()

    assert with_cache["cache"]["hits"] == 19 and with_cache["cache"]["misses"] == 1
    assert with_cache["sql_queries_total"] < without_cache["sql_queries_total"]
    assert without_cache["sql_queries_total"] >= 20  # una consulta por cada GET
