"""De dónde salen los usuarios y sus roles.

La central solo depende de la interfaz IdentityProvider. LocalDirectory guarda los usuarios en la base de la
central; LdapDirectory (central/ldap_directory.py) los valida contra un directorio LDAP / Active Directory y toma
el rol de los grupos. En los dos casos la tabla users guarda el perfil (rol, bodega, activo, último acceso).
"""

import base64
import hashlib
import hmac
import secrets
from typing import Optional, Protocol

from platform_common.auth import ROLES, SERVICE, WAREHOUSE_ROLES, Principal
from sqlalchemy.orm import Session

from central.config import settings
from central.models import UserEntity
from central.projection import now_utc

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt${}${}".format(base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: Optional[str]) -> bool:
    try:
        scheme, salt_b64, digest_b64 = (stored or "").split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = base64.b64decode(digest_b64)
    actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(expected, actual)


class IdentityError(ValueError):
    pass


class DirectoryUnavailable(RuntimeError):
    """El directorio no responde: la API contesta 503 en vez de tratarlo como contraseña incorrecta."""


class IdentityProvider(Protocol):
    name: str
    manages_users: bool

    def authenticate(self, db: Session, username: str, password: str) -> Optional[Principal]: ...

    def create_user(self, db: Session, username: str, display_name: str, role: str, warehouse_id: Optional[str],
                    password: str, created_by: str) -> UserEntity: ...

    def set_password(self, db: Session, username: str, password: str, temporary: bool) -> None: ...


def validate_assignment(role: str, warehouse_id: Optional[str]) -> None:
    if role not in ROLES or role == SERVICE:
        raise IdentityError(f"Rol no válido: {role}")
    if role in WAREHOUSE_ROLES and not warehouse_id:
        raise IdentityError("Los roles de bodega necesitan la bodega a la que pertenecen")
    if role not in WAREHOUSE_ROLES and warehouse_id:
        raise IdentityError("Solo los roles de bodega llevan bodega asignada")


class LocalDirectory:
    """Usuarios en la tabla users de la central, con contraseñas en scrypt."""

    name = "local"
    manages_users = True

    def authenticate(self, db: Session, username: str, password: str) -> Optional[Principal]:
        user = db.get(UserEntity, username.strip().lower())
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            return None
        user.last_login_at = now_utc()
        db.commit()
        return Principal(username=user.username, role=user.role, name=user.display_name, warehouse_id=user.warehouse_id)

    def create_user(self, db: Session, username: str, display_name: str, role: str, warehouse_id: Optional[str],
                    password: str, created_by: str) -> UserEntity:
        username = username.strip().lower()
        validate_assignment(role, warehouse_id)
        if db.get(UserEntity, username) is not None:
            raise IdentityError(f"El usuario {username} ya existe")
        user = UserEntity(username=username, display_name=display_name, role=role, warehouse_id=warehouse_id,
                          password_hash=hash_password(password), must_change_password=True, created_by=created_by)
        db.add(user)
        return user

    def set_password(self, db: Session, username: str, password: str, temporary: bool) -> None:
        user = db.get(UserEntity, username)
        if user is None:
            raise IdentityError(f"El usuario {username} no existe")
        user.password_hash, user.must_change_password = hash_password(password), temporary


_ldap = None


def get_identity_provider() -> IdentityProvider:
    global _ldap
    if settings.IDENTITY_PROVIDER == "local":
        return LocalDirectory()
    if settings.IDENTITY_PROVIDER == "ldap":
        if _ldap is None:  # una instancia por proceso: recuerda que la estructura del directorio ya está lista
            from central.ldap_directory import LdapDirectory
            _ldap = LdapDirectory()
        return _ldap
    raise IdentityError(f"IDENTITY_PROVIDER desconocido: {settings.IDENTITY_PROVIDER}")


def reset_provider() -> None:
    global _ldap
    _ldap = None


def temporary_password() -> str:
    return secrets.token_urlsafe(9)
