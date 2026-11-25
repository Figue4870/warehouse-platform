"""Usuarios en un directorio LDAP o Active Directory.

Inicio de sesión: la cuenta de servicio busca al usuario, su contraseña se valida con un bind propio y el rol sale de
los grupos a los que pertenece. La central no guarda contraseñas: solo el perfil (tabla users) para el tablero.
Con OpenLDAP (desarrollo y AWS) la central también crea usuarios, grupos y contraseñas; con un AD corporativo eso lo
hace el área de TI (LDAP_MANAGE_USERS=false).
"""

import base64
import hashlib
import logging
import re
import secrets
from typing import Optional, Protocol

from platform_common.auth import ADMIN_BODEGA, ADMIN_GLOBAL, LIDER, REGISTRADOR, Principal
from sqlalchemy.orm import Session

from central.config import settings
from central.identity import DirectoryUnavailable, IdentityError, validate_assignment
from central.models import UserEntity
from central.projection import now_utc

logger = logging.getLogger("central.ldap")

# Si alguien está en varios grupos, gana el de más alcance
PRECEDENCE = (ADMIN_GLOBAL, LIDER, ADMIN_BODEGA, REGISTRADOR)
OBJECT_CLASSES = {"inetOrgPerson": ["top", "person", "organizationalPerson", "inetOrgPerson"],
                  "groupOfNames": ["top", "groupOfNames"]}


def ssha(password: str) -> str:
    """Formato {SSHA} que OpenLDAP entiende al validar un bind."""
    salt = secrets.token_bytes(8)
    return "{SSHA}" + base64.b64encode(hashlib.sha1(password.encode("utf-8") + salt).digest() + salt).decode()


def escape_filter(value: str) -> str:
    """RFC 4515: un nombre de usuario no puede alterar el filtro de búsqueda (inyección LDAP)."""
    return "".join({"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\0": r"\00"}.get(c, c) for c in value)


def escape_dn(value: str) -> str:
    """RFC 4514 para el valor de un RDN (uid=..., cn=...)."""
    escaped = re.sub(r'([,+"\\<>;=])', r"\\\1", value)
    if escaped[:1] in ("#", " "):
        escaped = "\\" + escaped
    return escaped[:-1] + "\\ " if escaped.endswith(" ") else escaped


def first(values) -> Optional[str]:
    if isinstance(values, (list, tuple)):
        return str(values[0]) if values else None
    return str(values) if values not in (None, "") else None


class Gateway(Protocol):
    def search(self, base: str, search_filter: str, attributes: list[str]) -> list[tuple[str, dict]]: ...

    def check_password(self, dn: str, password: str) -> bool: ...

    def add(self, dn: str, object_classes: list[str], attributes: dict) -> bool: ...

    def add_value(self, dn: str, attribute: str, value: str) -> None: ...

    def replace(self, dn: str, attribute: str, values: list[str]) -> None: ...


class Ldap3Gateway:
    """Adaptador sobre ldap3. Una conexión por operación: los inicios de sesión son pocos y así no hay conexiones
    viejas que fallen tras un reinicio del directorio."""

    INVALID_CREDENTIALS, ALREADY_EXISTS, VALUE_EXISTS, NO_SUCH_OBJECT = 49, 68, 20, 32

    def __init__(self):
        try:
            import ldap3
        except ImportError as error:  # la imagen de la central la instala (requirements.txt)
            raise DirectoryUnavailable("Falta la librería ldap3") from error
        self.ldap3 = ldap3
        self.server = ldap3.Server(settings.LDAP_URL, get_info=ldap3.NONE, connect_timeout=settings.LDAP_TIMEOUT_SECONDS)

    def _bind(self, user: str, password: str):
        try:
            connection = self.ldap3.Connection(self.server, user=user, password=password, raise_exceptions=False,
                                               receive_timeout=settings.LDAP_TIMEOUT_SECONDS)
            connection.bind()
        except self.ldap3.core.exceptions.LDAPException as error:
            raise DirectoryUnavailable(f"El directorio no responde: {error}") from error
        return connection

    def _service(self):
        connection = self._bind(settings.LDAP_BIND_DN, settings.LDAP_BIND_PASSWORD)
        if not connection.bound:
            raise DirectoryUnavailable(f"La cuenta de servicio no pudo entrar al directorio: {connection.result.get('description')}")
        return connection

    def _run(self, operation):
        connection = self._service()
        try:
            return operation(connection)
        except self.ldap3.core.exceptions.LDAPException as error:
            raise DirectoryUnavailable(f"Error del directorio: {error}") from error
        finally:
            connection.unbind()

    def search(self, base, search_filter, attributes):
        def run(connection):
            connection.search(base, search_filter, search_scope=self.ldap3.SUBTREE, attributes=attributes)
            return [(item["dn"], dict(item.get("attributes") or {})) for item in connection.response or []
                    if item.get("type") == "searchResEntry"]
        return self._run(run)

    def check_password(self, dn, password):
        connection = self._bind(dn, password)
        try:
            return bool(connection.bound)
        finally:
            connection.unbind()

    def add(self, dn, object_classes, attributes):
        def run(connection):
            if connection.add(dn, object_classes, attributes):
                return True
            if connection.result.get("result") == self.ALREADY_EXISTS:
                return False
            raise DirectoryUnavailable(f"No se pudo crear {dn}: {connection.result.get('description')}")
        return self._run(run)

    def add_value(self, dn, attribute, value):
        def run(connection):
            ok = connection.modify(dn, {attribute: [(self.ldap3.MODIFY_ADD, [value])]})
            if not ok and connection.result.get("result") != self.VALUE_EXISTS:
                raise DirectoryUnavailable(f"No se pudo modificar {dn}: {connection.result.get('description')}")
        self._run(run)

    def replace(self, dn, attribute, values):
        def run(connection):
            if not connection.modify(dn, {attribute: [(self.ldap3.MODIFY_REPLACE, values)]}):
                raise DirectoryUnavailable(f"No se pudo modificar {dn}: {connection.result.get('description')}")
        self._run(run)


class LdapDirectory:
    name = "ldap"

    def __init__(self, gateway: Optional[Gateway] = None):
        self._gateway = gateway
        self._ready = False

    @property
    def manages_users(self) -> bool:
        return settings.LDAP_MANAGE_USERS

    @property
    def gateway(self) -> Gateway:
        if self._gateway is None:
            self._gateway = Ldap3Gateway()
        return self._gateway

    @property
    def users_base(self) -> str:
        return f"{settings.LDAP_USERS_OU},{settings.LDAP_BASE_DN}"

    @property
    def groups_base(self) -> str:
        return f"{settings.LDAP_GROUPS_OU},{settings.LDAP_BASE_DN}"

    @staticmethod
    def _templates() -> dict:
        return {ADMIN_GLOBAL: settings.LDAP_GROUP_ADMIN_GLOBAL, LIDER: settings.LDAP_GROUP_LIDER,
                ADMIN_BODEGA: settings.LDAP_GROUP_ADMIN_BODEGA, REGISTRADOR: settings.LDAP_GROUP_REGISTRADOR}

    def group_name(self, role: str, warehouse_id: Optional[str]) -> str:
        return self._templates()[role].format(warehouse=warehouse_id or "")

    def role_from_groups(self, groups: list[str]) -> tuple[Optional[str], Optional[str]]:
        names = sorted({g.lower() for g in groups})
        for role in PRECEDENCE:
            template = self._templates()[role].lower()
            if "{warehouse}" not in template:
                if template in names:
                    return role, None
                continue
            pattern = re.compile(re.escape(template).replace(re.escape("{warehouse}"), r"(wh-\d{3,})"))
            for name in names:
                if match := pattern.fullmatch(name):
                    return role, match.group(1).upper()
        return None, None

    def _find_user(self, username: str) -> Optional[tuple[str, dict]]:
        found = self.gateway.search(
            settings.LDAP_BASE_DN,
            f"(&(objectClass={settings.LDAP_USER_OBJECT_CLASS})({settings.LDAP_USER_ATTRIBUTE}={escape_filter(username)}))",
            ["cn", "displayName"])
        return found[0] if found else None

    def _groups_of(self, user_dn: str) -> list[str]:
        found = self.gateway.search(
            settings.LDAP_BASE_DN,
            f"(&(objectClass={settings.LDAP_GROUP_OBJECT_CLASS})(member={escape_filter(user_dn)}))", ["cn"])
        return [name for _, attributes in found if (name := first(attributes.get("cn")))]

    def ensure_ready(self, db: Session) -> None:
        """Unidades organizativas y, si hay LDAP_SEED_PASSWORD, los usuarios iniciales. Idempotente."""
        if self._ready or not self.manages_users:
            return
        for ou in (settings.LDAP_USERS_OU, settings.LDAP_GROUPS_OU):
            self.gateway.add(f"{ou},{settings.LDAP_BASE_DN}", ["top", "organizationalUnit"], {"ou": ou.split("=", 1)[1]})
        if settings.LDAP_SEED_PASSWORD:
            created = 0
            for user in db.query(UserEntity).filter(UserEntity.created_by == "seed").all():
                if self._find_user(user.username) is None:
                    self._add_user(user.username, user.display_name, settings.LDAP_SEED_PASSWORD, user.role, user.warehouse_id)
                    created += 1
            if created:
                logger.info("Usuarios iniciales creados en el directorio", extra={"event": "ldap_seeded", "context": {"users": created}})
        self._ready = True

    def _add_user(self, username: str, display_name: str, password: str, role: str, warehouse_id: Optional[str]) -> None:
        user_dn = f"{settings.LDAP_USER_ATTRIBUTE}={escape_dn(username)},{self.users_base}"
        surname = display_name.split()[-1] if display_name.split() else username
        attributes = {settings.LDAP_USER_ATTRIBUTE: username, "cn": display_name, "sn": surname,
                      "displayName": display_name, "userPassword": ssha(password)}
        classes = OBJECT_CLASSES.get(settings.LDAP_USER_OBJECT_CLASS, [settings.LDAP_USER_OBJECT_CLASS])
        if not self.gateway.add(user_dn, classes, attributes):
            raise IdentityError(f"El usuario {username} ya existe en el directorio")
        group = self.group_name(role, warehouse_id)
        group_dn = f"cn={escape_dn(group)},{self.groups_base}"
        group_classes = OBJECT_CLASSES.get(settings.LDAP_GROUP_OBJECT_CLASS, [settings.LDAP_GROUP_OBJECT_CLASS])
        # groupOfNames exige al menos un miembro: el grupo de una bodega nueva nace con su primer usuario
        if not self.gateway.add(group_dn, group_classes, {"cn": group, "member": user_dn}):
            self.gateway.add_value(group_dn, "member", user_dn)

    def authenticate(self, db: Session, username: str, password: str) -> Optional[Principal]:
        username = username.strip().lower()
        if not password:  # un bind con contraseña vacía es anónimo y el servidor lo acepta
            return None
        self.ensure_ready(db)
        found = self._find_user(username)
        if found is None:
            return None
        user_dn, attributes = found
        if not self.gateway.check_password(user_dn, password):
            return None
        role, warehouse_id = self.role_from_groups(self._groups_of(user_dn))
        if role is None:
            logger.warning("Usuario sin grupo de la plataforma", extra={"event": "ldap_no_role", "context": {"username": username}})
            return None
        display_name = first(attributes.get("displayName")) or first(attributes.get("cn")) or username
        profile = db.get(UserEntity, username)
        if profile is None:
            profile = UserEntity(username=username, display_name=display_name, role=role, created_by="directorio")
            db.add(profile)
        elif not profile.is_active:
            return None
        profile.display_name, profile.role, profile.warehouse_id = display_name, role, warehouse_id
        profile.last_login_at = now_utc()
        db.commit()
        return Principal(username=username, role=role, name=display_name, warehouse_id=warehouse_id)

    def create_user(self, db: Session, username: str, display_name: str, role: str, warehouse_id: Optional[str],
                    password: str, created_by: str) -> UserEntity:
        if not self.manages_users:
            raise IdentityError("Las credenciales se administran en el directorio corporativo")
        username = username.strip().lower()
        validate_assignment(role, warehouse_id)
        self.ensure_ready(db)
        if db.get(UserEntity, username) is not None or self._find_user(username) is not None:
            raise IdentityError(f"El usuario {username} ya existe")
        self._add_user(username, display_name, password, role, warehouse_id)
        user = UserEntity(username=username, display_name=display_name, role=role, warehouse_id=warehouse_id,
                          must_change_password=True, created_by=created_by)
        db.add(user)
        return user

    def set_password(self, db: Session, username: str, password: str, temporary: bool) -> None:
        if not self.manages_users:
            raise IdentityError("Las contraseñas se cambian en el directorio corporativo")
        found = self._find_user(username)
        if found is None:
            raise IdentityError(f"El usuario {username} no está en el directorio")
        self.gateway.replace(found[0], "userPassword", [ssha(password)])
        profile = db.get(UserEntity, username)
        if profile is not None:
            profile.must_change_password = temporary
