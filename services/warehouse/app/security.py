"""Autenticación y autorización de la API de bodega.

Los tokens los emite la central. La bodega guarda sus claves públicas en su propia base de datos:
así sigue verificando sesiones aunque la central no esté disponible.
"""

import logging
import threading
import time
from datetime import datetime, timezone

import httpx
from fastapi import Depends, HTTPException, Request, status
from platform_common.auth import (
    ADMIN_BODEGA,
    ADMIN_GLOBAL,
    LIDER,
    REGISTRADOR,
    SERVICE,
    AuthError,
    Principal,
    TokenVerifier,
    key_id_of,
    parse_public_keys,
)
from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, get_db

logger = logging.getLogger("warehouse.security")

ANY_USER = (ADMIN_GLOBAL, LIDER, ADMIN_BODEGA, REGISTRADOR, SERVICE)
OPERATORS = (ADMIN_BODEGA, REGISTRADOR)
WAREHOUSE_ADMINS = (ADMIN_BODEGA, ADMIN_GLOBAL)
SYSTEM = Principal(username="system", role=ADMIN_GLOBAL, name="Sistema")


class TrustedKeyEntity(Base):
    """Claves públicas de la central con las que esta bodega verifica tokens."""

    __tablename__ = "trusted_keys"

    kid = Column(String(40), primary_key=True)
    public_key = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)


class KeyStore:
    def __init__(self):
        self.verifier = TokenVerifier(parse_public_keys(settings.AUTH_PUBLIC_KEYS))
        self._last_fetch = 0.0
        self._lock = threading.Lock()

    def _load_from_db(self, db: Session) -> None:
        for row in db.query(TrustedKeyEntity).all():
            if not self.verifier.knows(row.kid):
                self.verifier.add_key(row.kid, row.public_key)

    def _fetch_from_central(self, db: Session) -> None:
        with self._lock:
            if time.monotonic() - self._last_fetch < settings.AUTH_KEYS_REFRESH_SECONDS:
                return
            self._last_fetch = time.monotonic()
        try:
            response = httpx.get(f"{settings.CENTRAL_API_URL.rstrip('/')}/auth/keys", timeout=2.0)
            response.raise_for_status()
            keys = response.json().get("keys", [])
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("No se pudieron leer las claves de la central", extra={"event": "auth_keys_fetch_failed",
                           "context": {"error": type(error).__name__}})
            return
        now = datetime.now(timezone.utc)
        for item in keys:
            public_key = item.get("public_key")
            if not public_key or key_id_of(public_key) != item.get("kid"):
                continue
            if db.get(TrustedKeyEntity, item["kid"]) is None:
                db.add(TrustedKeyEntity(kid=item["kid"], public_key=public_key, fetched_at=now))
            self.verifier.add_key(item["kid"], public_key)
        db.commit()

    def verify(self, token: str, db: Session) -> Principal:
        kid = TokenVerifier.key_id(token)
        if not self.verifier.knows(kid):
            self._load_from_db(db)
        if not self.verifier.knows(kid):
            self._fetch_from_central(db)
        return self.verifier.verify(token)


key_store = KeyStore()


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    if not settings.AUTH_ENABLED:
        return SYSTEM
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        principal = key_store.verify(header[7:].strip(), db)
    except AuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error),
                            headers={"WWW-Authenticate": "Bearer"}) from error
    request.state.principal = principal
    return principal


def require(*roles: str):
    """Dependencia que exige uno de los roles y, si es un rol de bodega, que sea de esta bodega."""

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not settings.AUTH_ENABLED:
            return principal
        if principal.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu rol no tiene permiso para esta acción")
        if not principal.can_act_on(settings.WAREHOUSE_ID):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Tu usuario pertenece a {principal.warehouse_id}, no a {settings.WAREHOUSE_ID}")
        return principal

    return dependency
