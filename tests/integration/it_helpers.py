"""Ayudas de las pruebas contra la plataforma levantada: usuarios reales, catálogo universal y tokens.

Variables: API_URL (WH-001), API_URL_2 (WH-002), CENTRAL_URL, REDIS_URL e IT_PASSWORD (la de los usuarios semilla).
"""

import os
import time
import uuid
from functools import lru_cache

import httpx
import pytest

API_URL = os.getenv("API_URL", "http://localhost:8001")
API_URL_2 = os.getenv("API_URL_2", "http://localhost:8002")
CENTRAL_URL = os.getenv("CENTRAL_URL", "http://localhost:8090")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")
PASSWORD = os.getenv("IT_PASSWORD", "Bodegas2026!")
ADMIN_GLOBAL, ADMIN_WH1, ADMIN_WH2 = "admin.global", "admin.wh001", "admin.wh002"


def wait_until(check, timeout=60.0, every=1.0, what="la plataforma"):
    start, last = time.time(), None
    while time.time() - start < timeout:
        try:
            last = check()
            if last:
                return last
        except (httpx.HTTPError, KeyError, ValueError, StopIteration) as error:
            last = error
        time.sleep(every)
    pytest.fail(f"Se acabó el tiempo esperando {what}: {last}")


def reachable(url: str) -> bool:
    try:
        httpx.get(f"{url}/health", timeout=3)
        return True
    except httpx.HTTPError:
        return False


@lru_cache
def token(username: str) -> str:
    response = httpx.post(f"{CENTRAL_URL}/auth/login", json={"username": username, "password": PASSWORD}, timeout=10)
    if response.status_code != 200:
        pytest.skip(f"No se pudo iniciar sesión como {username} en {CENTRAL_URL}: {response.text[:120]}")
    return response.json()["access_token"]


def auth(username: str) -> dict:
    return {"Authorization": f"Bearer {token(username)}"}


def client(url: str, username: str) -> httpx.Client:
    return httpx.Client(base_url=url, timeout=20, headers=auth(username))


def universal_sku(central, prefix="IT") -> str:
    """Crea el SKU en el catálogo universal (como haría el administrador global)."""
    sku = f"{prefix}-{uuid.uuid4().hex[:10].upper()}"
    response = central.post("/catalog", json={"sku": sku, "name": f"Integración {sku}", "unit": "UNIDAD",
                                              "supplier_cost": "1000.00", "retail_price": "2000.00"})
    assert response.status_code == 201, response.text
    return sku


def local_product(warehouse, sku, initial_stock=0):
    """La bodega recibe el SKU por su cola de comandos y lo incorpora a su catálogo."""
    wait_until(lambda: any(p["sku"] == sku for p in warehouse.get("/catalog/universal", params={"search": sku}).json()),
               timeout=45, what=f"que {sku} llegue al catálogo de la bodega")
    response = warehouse.post("/products", json={"sku": sku, "name": f"Integración {sku}", "unit": "UNIDAD"})
    assert response.status_code == 201, response.text
    product = response.json()
    if initial_stock:
        r = warehouse.post("/inventory/movements", json={"product_id": product["id"], "type": "RECEIPT", "quantity": initial_stock})
        assert r.status_code == 201, r.text
    return product
