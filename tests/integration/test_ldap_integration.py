"""Directorio LDAP real (OpenLDAP de compose-central.yml): inicio de sesión, alta de un líder y cambio de contraseña.

Se salta si la central usa el directorio local (IDENTITY_PROVIDER=local).
"""

import uuid

import httpx
import pytest

from it_helpers import CENTRAL_URL, PASSWORD


@pytest.fixture(scope="module")
def ldap_central(central):
    settings = central.get("/admin/settings").json()
    if settings.get("identity_provider") != "ldap":
        pytest.skip("La central usa el directorio local")
    return central


def login(username, password):
    return httpx.post(f"{CENTRAL_URL}/auth/login", json={"username": username, "password": password}, timeout=10)


def test_los_usuarios_iniciales_entran_por_el_directorio(ldap_central):
    for username, role in (("admin.global", "ADMIN_GLOBAL"), ("lider", "LIDER"), ("registrador.wh001", "REGISTRADOR")):
        response = login(username, PASSWORD)
        assert response.status_code == 200, response.text
        assert response.json()["user"]["role"] == role
    assert login("lider", "no-es-la-clave").status_code == 401


def test_alta_de_un_lider_y_cambio_de_contrasena_en_el_directorio(ldap_central):
    username = f"lider.it{uuid.uuid4().hex[:6]}"
    created = ldap_central.post("/admin/users", json={"username": username, "display_name": "Líder de prueba", "role": "LIDER"})
    assert created.status_code == 201, created.text
    temporary = created.json()["temporary_password"]
    first = login(username, temporary)
    assert first.status_code == 200 and first.json()["user"]["role"] == "LIDER"
    headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
    changed = httpx.post(f"{CENTRAL_URL}/auth/password", headers=headers, timeout=10,
                         json={"current_password": temporary, "new_password": "Clave-Integracion-2026"})
    assert changed.status_code == 200, changed.text
    assert login(username, temporary).status_code == 401
    assert login(username, "Clave-Integracion-2026").status_code == 200
