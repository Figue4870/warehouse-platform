"""Tokens de acceso firmados con Ed25519 (JWT, algoritmo EdDSA).

La central firma con su clave privada; las bodegas verifican con la clave pública, sin llamar a la
central en cada petición. Cada clave tiene un identificador (kid) para poder rotarla.
"""

import base64
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ISSUER = "warehouse-platform-central"
AUDIENCE = "warehouse-platform"
ALGORITHM = "EdDSA"

ADMIN_GLOBAL = "ADMIN_GLOBAL"
LIDER = "LIDER"
ADMIN_BODEGA = "ADMIN_BODEGA"
REGISTRADOR = "REGISTRADOR"
SERVICE = "SERVICE"

ROLES = (ADMIN_GLOBAL, LIDER, ADMIN_BODEGA, REGISTRADOR, SERVICE)
WAREHOUSE_ROLES = (ADMIN_BODEGA, REGISTRADOR)


class AuthError(Exception):
    """Token ausente, inválido, vencido o sin permisos suficientes."""


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    name: str = ""
    warehouse_id: Optional[str] = None

    def can_act_on(self, warehouse_id: str) -> bool:
        """Los roles de bodega solo operan sobre su propia bodega."""
        return self.role not in WAREHOUSE_ROLES or self.warehouse_id == warehouse_id

    def as_dict(self) -> dict:
        return {"username": self.username, "role": self.role, "name": self.name, "warehouse_id": self.warehouse_id}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def generate_private_key() -> str:
    """Semilla Ed25519 de 32 bytes en base64."""
    return _b64(secrets.token_bytes(32))


def public_key_of(private_b64: str) -> str:
    private = Ed25519PrivateKey.from_private_bytes(_unb64(private_b64))
    return _b64(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def key_id_of(public_b64: str) -> str:
    """Identificador corto y estable de una clave pública."""
    return public_b64.rstrip("=")[:12]


class TokenSigner:
    def __init__(self, private_b64: str):
        self._private = Ed25519PrivateKey.from_private_bytes(_unb64(private_b64))
        self.public_b64 = public_key_of(private_b64)
        self.kid = key_id_of(self.public_b64)

    def issue(self, principal: Principal, ttl: timedelta, now: Optional[datetime] = None) -> str:
        now = now or datetime.now(timezone.utc)
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": principal.username,
            "name": principal.name,
            "role": principal.role,
            "wh": principal.warehouse_id,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(claims, self._private, algorithm=ALGORITHM, headers={"kid": self.kid})


class TokenVerifier:
    """Verifica tokens con un conjunto de claves públicas {kid: clave_base64}."""

    def __init__(self, public_keys: Optional[dict[str, str]] = None, leeway_seconds: int = 30):
        self._keys: dict[str, Ed25519PublicKey] = {}
        self.leeway = leeway_seconds
        for kid, public_b64 in (public_keys or {}).items():
            self.add_key(kid, public_b64)

    def add_key(self, kid: str, public_b64: str) -> None:
        self._keys[kid] = Ed25519PublicKey.from_public_bytes(_unb64(public_b64))

    def knows(self, kid: str) -> bool:
        return kid in self._keys

    @staticmethod
    def key_id(token: str) -> Optional[str]:
        try:
            return jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as error:
            raise AuthError("Token mal formado") from error

    def verify(self, token: str) -> Principal:
        kid = self.key_id(token)
        key = self._keys.get(kid)
        if key is None:
            raise AuthError("Token firmado con una clave desconocida")
        try:
            claims = jwt.decode(token, key, algorithms=[ALGORITHM], audience=AUDIENCE, issuer=ISSUER,
                                leeway=self.leeway, options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        except jwt.ExpiredSignatureError as error:
            raise AuthError("La sesión venció; vuelve a iniciar sesión") from error
        except jwt.PyJWTError as error:
            raise AuthError("Token inválido") from error
        role = claims.get("role")
        if role not in ROLES:
            raise AuthError("Rol desconocido en el token")
        return Principal(username=claims["sub"], role=role, name=claims.get("name") or "", warehouse_id=claims.get("wh"))


def parse_public_keys(text: str) -> dict[str, str]:
    """Convierte "clave1,clave2" (claves públicas en base64) en {kid: clave}."""
    return {key_id_of(value): value for value in filter(None, (part.strip() for part in (text or "").split(",")))}
