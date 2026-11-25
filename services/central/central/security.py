"""Firma y verificación de tokens en la central, y permisos de cada endpoint."""

import threading
import time
from datetime import timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from platform_common.auth import (
    ADMIN_BODEGA,
    ADMIN_GLOBAL,
    LIDER,
    REGISTRADOR,
    SERVICE,
    AuthError,
    Principal,
    TokenSigner,
    TokenVerifier,
    generate_private_key,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from central.config import settings
from central.database import get_db
from central.models import SigningKeyEntity

ANY_USER = (ADMIN_GLOBAL, LIDER, ADMIN_BODEGA, REGISTRADOR, SERVICE)
MANAGEMENT = (ADMIN_GLOBAL, LIDER)
SYSTEM = Principal(username="system", role=ADMIN_GLOBAL, name="Sistema")

_lock = threading.Lock()
_signer: Optional[TokenSigner] = None
_verifier: Optional[TokenVerifier] = None
_service_token: tuple[str, float] = ("", 0.0)


def _load_signer(db: Session) -> TokenSigner:
    if settings.AUTH_SIGNING_KEY:
        return TokenSigner(settings.AUTH_SIGNING_KEY)
    row = db.query(SigningKeyEntity).filter(SigningKeyEntity.active.is_(True)).order_by(SigningKeyEntity.created_at).first()
    if row is None:
        private = generate_private_key()
        signer = TokenSigner(private)
        db.add(SigningKeyEntity(kid=signer.kid, private_key=private, public_key=signer.public_b64, active=True))
        try:
            db.commit()
            return signer
        except IntegrityError:
            db.rollback()  # otra instancia la creó al mismo tiempo
            row = db.query(SigningKeyEntity).filter(SigningKeyEntity.active.is_(True)).first()
    return TokenSigner(row.private_key)


def signer(db: Session) -> TokenSigner:
    global _signer
    with _lock:
        if _signer is None:
            _signer = _load_signer(db)
        return _signer


def reset_cache() -> None:
    global _signer, _verifier, _service_token
    with _lock:
        _signer, _verifier, _service_token = None, None, ("", 0.0)


def public_keys(db: Session) -> list[dict]:
    current = signer(db)
    keys = {current.kid: current.public_b64}
    for row in db.query(SigningKeyEntity).all():
        keys.setdefault(row.kid, row.public_key)
    return [{"kid": kid, "alg": "EdDSA", "public_key": key} for kid, key in keys.items()]


def issue_token(db: Session, principal: Principal) -> str:
    return signer(db).issue(principal, timedelta(minutes=settings.AUTH_TOKEN_TTL_MINUTES))


def service_token(db: Session) -> str:
    """Token de la propia central para llamar a las bodegas (/sync y consultas en vivo)."""
    global _service_token
    token, expires = _service_token
    if token and time.monotonic() < expires - 60:
        return token
    ttl = settings.SERVICE_TOKEN_TTL_MINUTES
    token = signer(db).issue(Principal(username=f"central:{settings.INSTANCE_NAME}", role=SERVICE, name="Central"),
                             timedelta(minutes=ttl))
    _service_token = (token, time.monotonic() + ttl * 60)
    return token


def service_headers(db: Session) -> dict:
    return {"Authorization": f"Bearer {service_token(db)}"} if settings.AUTH_ENABLED else {}


def _verifier_for(token: str, db: Session) -> TokenVerifier:
    global _verifier
    kid = TokenVerifier.key_id(token)
    if _verifier is None or not _verifier.knows(kid):
        _verifier = TokenVerifier({item["kid"]: item["public_key"] for item in public_keys(db)})
    return _verifier


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    if not settings.AUTH_ENABLED:
        return SYSTEM
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión para continuar",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        principal = _verifier_for(header[7:].strip(), db).verify(header[7:].strip())
    except AuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error),
                            headers={"WWW-Authenticate": "Bearer"}) from error
    request.state.principal = principal
    return principal


def require(*roles: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if settings.AUTH_ENABLED and principal.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu rol no tiene permiso para esta acción")
        return principal

    return dependency
