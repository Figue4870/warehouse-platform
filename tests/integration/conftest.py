"""Fixtures de las pruebas contra la plataforma levantada (bodegas + central)."""

import pytest

from it_helpers import (
    ADMIN_GLOBAL,
    ADMIN_WH1,
    ADMIN_WH2,
    API_URL,
    API_URL_2,
    CENTRAL_URL,
    REDIS_URL,
    client,
    local_product,
    reachable,
    universal_sku,
)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def api_url():
    return API_URL


@pytest.fixture(scope="session")
def central():
    if not reachable(CENTRAL_URL):
        pytest.skip(f"La central no responde en {CENTRAL_URL} (inicia sesión y firma los tokens).")
    with client(CENTRAL_URL, ADMIN_GLOBAL) as c:
        yield c


@pytest.fixture(scope="session")
def api(central):
    if not reachable(API_URL):
        pytest.skip(f"La API no responde en {API_URL}. Levántala con docker compose antes de estas pruebas.")
    with client(API_URL, ADMIN_WH1) as c:
        yield c


@pytest.fixture(scope="session")
def wh2(central):
    if not reachable(API_URL_2):
        pytest.skip(f"WH-002 no responde en {API_URL_2}")
    with client(API_URL_2, ADMIN_WH2) as c:
        yield c


@pytest.fixture(scope="session")
def redis_client():
    try:
        import redis

        redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        redis_client.ping()
        return redis_client
    except Exception:
        pytest.skip(f"No se pudo conectar a Redis en {REDIS_URL}")


@pytest.fixture
def new_product(api, central):
    """Producto con SKU único en WH-001 (las pruebas no chocan con datos existentes)."""

    def _create(initial_stock: int = 0):
        return local_product(api, universal_sku(central), initial_stock)

    return _create
