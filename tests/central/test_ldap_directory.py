"""Directorio LDAP: inicio de sesión con bind, rol según grupos, altas desde el Admin global y caídas del directorio.

El servidor se reemplaza por un directorio en memoria con la misma interfaz que el adaptador de ldap3; la prueba
contra OpenLDAP real está en tests/integration/test_ldap_integration.py.
"""

import base64
import hashlib
import re

import pytest
from platform_common import auth

from central import identity, security
from central.config import settings
from central.identity import DirectoryUnavailable
from central.ldap_directory import LdapDirectory, escape_filter, ssha
from central.models import UserEntity

PASSWORD = "Clave-Inicial-2026"


def _unescape(value):
    return re.sub(r"\\([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), value)


class FakeDirectory:
    """Lo mínimo de un servidor LDAP: entradas por DN, filtros (&(a=b)(c=d)), bind con {SSHA}."""

    def __init__(self):
        self.entries, self.down, self.searches = {}, False, []

    def _check(self):
        if self.down:
            raise DirectoryUnavailable("El directorio no responde")

    def search(self, base, search_filter, attributes):
        self._check()
        self.searches.append(search_filter)
        conditions = [(a.lower(), _unescape(v).lower()) for a, v in re.findall(r"\(([^=()&|!]+)=((?:[^()\\]|\\[0-9a-fA-F]{2})*)\)", search_filter)]
        found = []
        for dn, attrs in self.entries.values():
            if not dn.lower().endswith(base.lower()):
                continue
            if all(any(str(v).lower() == value for v in attrs.get(name, [])) for name, value in conditions):
                found.append((dn, {k: attrs[k] for k in attributes if k in attrs}))
        return found

    def check_password(self, dn, password):
        self._check()
        stored = self.entries.get(dn.lower(), (None, {}))[1].get("userpassword", [""])[0]
        if not stored.startswith("{SSHA}"):
            return False
        raw = base64.b64decode(stored[6:])
        return hashlib.sha1(password.encode() + raw[20:]).digest() == raw[:20]

    def add(self, dn, object_classes, attributes):
        self._check()
        if dn.lower() in self.entries:
            return False
        attrs = {k.lower(): (v if isinstance(v, list) else [v]) for k, v in attributes.items()}
        attrs["objectclass"] = list(object_classes)
        self.entries[dn.lower()] = (dn, attrs)
        return True

    def add_value(self, dn, attribute, value):
        self._check()
        values = self.entries[dn.lower()][1].setdefault(attribute.lower(), [])
        if value not in values:
            values.append(value)

    def replace(self, dn, attribute, values):
        self._check()
        self.entries[dn.lower()][1][attribute.lower()] = list(values)

    def members(self, group):
        return self.entries[f"cn={group},ou=grupos,dc=bodegas,dc=local".lower()][1]["member"]


@pytest.fixture
def ldap(central_sf, monkeypatch):
    fake = FakeDirectory()
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "IDENTITY_PROVIDER", "ldap")
    monkeypatch.setattr(settings, "LDAP_SEED_PASSWORD", PASSWORD)
    monkeypatch.setattr(identity, "_ldap", LdapDirectory(gateway=fake))
    security.reset_cache()
    with central_sf() as db:  # perfiles iniciales, como los de central_seed.sql
        for username, role, warehouse_id in (("admin.global", auth.ADMIN_GLOBAL, None), ("lider", auth.LIDER, None),
                                             ("registrador.wh002", auth.REGISTRADOR, "WH-002")):
            db.add(UserEntity(username=username, display_name=username.title(), role=role, warehouse_id=warehouse_id, created_by="seed"))
        db.commit()
    yield fake
    identity.reset_provider()
    security.reset_cache()


def login(client, username, password=PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


def bearer(response):
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_los_usuarios_iniciales_se_crean_en_el_directorio_y_el_rol_sale_del_grupo(central_client, ldap):
    response = login(central_client, "registrador.wh002")
    assert response.status_code == 200, response.text
    assert response.json()["user"] == {"username": "registrador.wh002", "role": "REGISTRADOR", "name": "Registrador.Wh002",
                                       "warehouse_id": "WH-002"}
    assert "ou=usuarios,dc=bodegas,dc=local" in ldap.entries and "ou=grupos,dc=bodegas,dc=local" in ldap.entries
    assert ldap.members("Registradores-WH-002") == ["uid=registrador.wh002,ou=usuarios,dc=bodegas,dc=local"]
    stored = ldap.entries["uid=registrador.wh002,ou=usuarios,dc=bodegas,dc=local"][1]["userpassword"][0]
    assert stored.startswith("{SSHA}") and PASSWORD not in stored   # el directorio nunca guarda la contraseña en claro


def test_contrasena_incorrecta_vacia_o_usuario_inexistente(central_client, ldap):
    assert login(central_client, "lider", "otra-clave").status_code == 401
    assert login(central_client, "nadie").status_code == 401
    assert LdapDirectory(gateway=ldap).authenticate(None, "lider", "") is None   # bind anónimo: nunca cuenta como acceso


def test_un_nombre_de_usuario_no_puede_alterar_el_filtro(central_client, ldap):
    login(central_client, "lider")
    assert login(central_client, "*)(uid=*").status_code == 401
    assert escape_filter("*)(uid=*") == r"\2a\29\28uid=\2a" and r"\2a\29\28uid=\2a" in ldap.searches[-1]


def test_el_grupo_de_mas_alcance_define_el_rol(ldap):
    directory = LdapDirectory(gateway=ldap)
    assert directory.role_from_groups(["Registradores-WH-001", "Lideres"]) == (auth.LIDER, None)
    assert directory.role_from_groups(["administradores-wh-003"]) == (auth.ADMIN_BODEGA, "WH-003")
    assert directory.role_from_groups(["Contabilidad"]) == (None, None)


def test_usuario_del_directorio_sin_grupo_de_la_plataforma_no_entra(central_client, ldap):
    login(central_client, "lider")   # deja lista la estructura
    ldap.add("uid=contador,ou=usuarios,dc=bodegas,dc=local", ["inetOrgPerson"],
             {"uid": "contador", "cn": "Contador", "sn": "Contador", "userPassword": ssha(PASSWORD)})
    assert login(central_client, "contador").status_code == 401


def test_el_admin_global_crea_un_lider_en_el_directorio(central_client, ldap):
    headers = bearer(login(central_client, "admin.global"))
    created = central_client.post("/admin/users", headers=headers,
                                  json={"username": "lider.ventas", "display_name": "Ana Gómez", "role": "LIDER"})
    assert created.status_code == 201, created.text
    assert "uid=lider.ventas,ou=usuarios,dc=bodegas,dc=local" in ldap.members("Lideres")
    first = login(central_client, "lider.ventas", created.json()["temporary_password"])
    assert first.status_code == 200 and first.json()["user"]["role"] == "LIDER"
    assert central_client.get("/auth/me", headers=bearer(first)).json()["must_change_password"] is True
    changed = central_client.post("/auth/password", headers=bearer(first),
                                  json={"current_password": created.json()["temporary_password"], "new_password": "Nueva-Clave-2026"})
    assert changed.status_code == 200
    assert login(central_client, "lider.ventas", created.json()["temporary_password"]).status_code == 401
    assert login(central_client, "lider.ventas", "Nueva-Clave-2026").status_code == 200


def test_bodega_nueva_crea_sus_grupos_en_el_directorio(central_client, ldap):
    headers = bearer(login(central_client, "admin.global"))
    response = central_client.post("/admin/warehouses", headers=headers, json={
        "nit": "900777888-1", "name": "Bodega Buga", "location": "Buga", "address": "Carrera 14 # 5-32", "capacity_units": 5000})
    assert response.status_code == 201, response.text
    warehouse_id = response.json()["warehouse_id"]
    credentials = {c["role"]: c for c in response.json()["initial_credentials"]}
    assert ldap.members(f"Administradores-{warehouse_id}") and ldap.members(f"Registradores-{warehouse_id}")
    admin = login(central_client, credentials["ADMIN_BODEGA"]["username"], credentials["ADMIN_BODEGA"]["temporary_password"])
    assert admin.json()["user"] == {"username": f"admin.{warehouse_id.lower().replace('-', '')}", "role": "ADMIN_BODEGA",
                                    "name": "Administrador Bodega Buga", "warehouse_id": warehouse_id}


def test_nueva_contrasena_temporal_desde_el_admin_global(central_client, ldap):
    headers = bearer(login(central_client, "admin.global"))
    login(central_client, "lider")
    reset = central_client.post("/admin/users/lider/reset-password", headers=headers)
    assert reset.status_code == 200
    assert login(central_client, "lider").status_code == 401
    assert login(central_client, "lider", reset.json()["temporary_password"]).status_code == 200


def test_un_perfil_desactivado_no_entra_aunque_el_directorio_lo_acepte(central_client, ldap):
    headers = bearer(login(central_client, "admin.global"))
    login(central_client, "lider")
    assert central_client.post("/admin/users/lider/active", params={"active": "false"}, headers=headers).status_code == 200
    assert login(central_client, "lider").status_code == 401


def test_directorio_caido_responde_503_y_no_401(central_client, ldap):
    login(central_client, "lider")
    ldap.down = True
    response = login(central_client, "lider")
    assert response.status_code == 503 and "directorio" in response.json()["detail"]


def test_con_un_ad_corporativo_las_altas_se_hacen_alla(central_client, ldap, monkeypatch):
    headers = bearer(login(central_client, "admin.global"))
    monkeypatch.setattr(settings, "LDAP_MANAGE_USERS", False)
    created = central_client.post("/admin/users", headers=headers,
                                  json={"username": "otro.lider", "display_name": "Otro Líder", "role": "LIDER"})
    assert created.status_code == 422 and "directorio corporativo" in created.json()["detail"]
    assert central_client.get("/admin/settings", headers=headers).json()["manages_users"] is False
