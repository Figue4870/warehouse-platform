"""Inicio de sesión y claves públicas para verificar los tokens."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from platform_common.auth import Principal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from central import security
from central.config import settings
from central.database import get_db
from central.identity import IdentityError, get_identity_provider
from central.models import UserEntity
from central.security import ANY_USER, require

router = APIRouter(prefix="/auth", tags=["Autenticación"])
logger = logging.getLogger("central.auth")


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


@router.post("/login", summary="Iniciar sesión: devuelve un token para las APIs de la central y de las bodegas")
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    principal = get_identity_provider().authenticate(db, credentials.username, credentials.password)
    if principal is None:
        logger.warning("Inicio de sesión fallido", extra={"event": "login_failed", "context": {"username": credentials.username}})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario o contraseña incorrectos")
    logger.info("Inicio de sesión", extra={"event": "login_ok", "context": principal.as_dict()})
    return {
        "access_token": security.issue_token(db, principal),
        "token_type": "bearer",
        "expires_in": settings.AUTH_TOKEN_TTL_MINUTES * 60,
        "user": principal.as_dict(),
    }


@router.get("/keys", summary="Claves públicas (las bodegas las guardan para verificar tokens sin llamar a la central)")
def keys(db: Session = Depends(get_db)):
    return {"keys": security.public_keys(db)}


@router.get("/me", summary="Usuario de la sesión actual")
def me(db: Session = Depends(get_db), principal: Principal = Depends(require(*ANY_USER))):
    user = db.get(UserEntity, principal.username)
    return {**principal.as_dict(), "must_change_password": bool(user and user.must_change_password)}


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=10, max_length=200)


@router.post("/password", summary="Cambiar la contraseña (obligatorio tras recibir una temporal)")
def change_password(data: PasswordChange, db: Session = Depends(get_db), principal: Principal = Depends(require(*ANY_USER))):
    provider = get_identity_provider()
    if provider.authenticate(db, principal.username, data.current_password) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="La contraseña actual no es correcta")
    try:
        provider.set_password(db, principal.username, data.new_password, temporary=False)
    except IdentityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    db.commit()
    return {"changed": True}
