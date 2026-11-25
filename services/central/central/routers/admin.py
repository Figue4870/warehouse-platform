"""Administración global: registro de bodegas y credenciales de usuarios."""

import logging
import re
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from platform_common.auth import ADMIN_BODEGA, ADMIN_GLOBAL, REGISTRADOR, Principal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from central import platform_commands
from central.config import default_api_url, settings
from central.database import get_db
from central.identity import IdentityError, get_identity_provider, temporary_password
from central.models import UserEntity, WarehouseEntity
from central.notifications import INFO, notify
from central.projection import now_utc
from central.security import require

router = APIRouter(prefix="/admin", tags=["Administración global"])
logger = logging.getLogger("central.admin")

admin_only = require(ADMIN_GLOBAL)


class WarehouseRegistration(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    nit: str = Field(..., pattern=r"^\d{6,12}(-\d)?$", description="NIT con dígito de verificación opcional: 900123456-7")
    name: str = Field(..., min_length=3, max_length=200)
    location: str = Field(..., min_length=2, max_length=120, description="Ciudad o región")
    address: str = Field(..., min_length=5, max_length=250)
    description: Optional[str] = Field(None, max_length=1000)
    capacity_units: int = Field(..., gt=0, le=10_000_000)
    warehouse_id: Optional[str] = Field(None, pattern=r"^WH-\d{3,}$")


class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    username: str = Field(..., pattern=r"^[a-z0-9._-]{3,60}$")
    display_name: str = Field(..., min_length=3, max_length=200)
    role: str
    warehouse_id: Optional[str] = Field(None, pattern=r"^WH-\d{3,}$")


def _user_view(user: UserEntity) -> dict:
    return {"username": user.username, "display_name": user.display_name, "role": user.role,
            "warehouse_id": user.warehouse_id, "is_active": user.is_active,
            "must_change_password": user.must_change_password, "created_by": user.created_by,
            "created_at": user.created_at, "last_login_at": user.last_login_at}


def _next_warehouse_id(db: Session) -> str:
    numbers = [int(m.group(1)) for (wid,) in db.query(WarehouseEntity.warehouse_id).all()
               if (m := re.fullmatch(r"WH-(\d+)", wid))]
    numbers += [int(wid[3:]) for wid in platform_commands.known_warehouses(db) if re.fullmatch(r"WH-\d+", wid)]
    return f"WH-{(max(numbers) if numbers else 0) + 1:03d}"


def provisioning_env(warehouse_id: str, heartbeat_secret: str) -> str:
    """Archivo .env de la bodega nueva (mismo esquema de puertos que infrastructure/docker/components/wh_00X)."""
    n = int(warehouse_id[3:])
    return "\n".join([
        f"WH_ID={warehouse_id}", "",
        "DB_USER=postgres", "DB_PASSWORD=postgres", "DB_NAME=warehouse_db", f"DB_PORT={5432 + n}", "",
        f"BE_PORT={8000 + n}", f"FE_PORT={8080 + n}", "",
        f"REDIS_PORT={6379 + n}", "CACHE_ENABLED=true", "CACHE_TTL_SECONDS=30", "",
        "RABBIT_USER=warehouse", "RABBIT_PASSWORD=warehouse", f"RABBIT_PORT={5672 + n}", f"RABBIT_MGMT_PORT={15672 + n}", "",
        "CACHE_INVALIDATION_MODE=both", "REPORT_SIMULATED_DELAY_SECONDS=0", "",
        "CENTRAL_AMQP_URL=amqp://platform:platform@central_rabbitmq:5672/%2F",
        f"HEARTBEAT_SECRET={heartbeat_secret}", "",
    ])


@router.post("/warehouses", status_code=status.HTTP_201_CREATED, summary="Registrar una bodega nueva y aprovisionar lo que necesita")
def register_warehouse(data: WarehouseRegistration, db: Session = Depends(get_db), principal: Principal = Depends(admin_only)):
    """Deja lista la parte de plataforma: registro, clave del heartbeat, cola exclusiva de comandos con el
    catálogo universal y usuarios de bodega. La infraestructura (base local, Redis, RabbitMQ y APIs) se levanta
    con el .env que devuelve esta respuesta, o con Terraform agregando la bodega al mapa warehouses."""
    if db.query(WarehouseEntity).filter(WarehouseEntity.nit == data.nit).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Ya hay una bodega con NIT {data.nit}")
    warehouse_id = data.warehouse_id or _next_warehouse_id(db)
    warehouse = db.get(WarehouseEntity, warehouse_id)
    if warehouse is not None and warehouse.registered_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{warehouse_id} ya está registrada")
    if warehouse is None:
        warehouse = WarehouseEntity(warehouse_id=warehouse_id, first_seen_at=now_utc(), watermark_updated_at=now_utc(),
                                    reported_sequence=0, max_received_sequence=0, watermark_sequence=0,
                                    heartbeat_failures=0, heartbeat_rejections=0)
        db.add(warehouse)
    secret = f"wh-{secrets.token_urlsafe(24)}"
    suffix = warehouse_id.lower().replace("-", "")
    admin_username = f"admin.{suffix}"
    warehouse.nit, warehouse.name, warehouse.location, warehouse.address = data.nit, data.name, data.location, data.address
    warehouse.description, warehouse.capacity_units = data.description, data.capacity_units
    warehouse.admin_username, warehouse.heartbeat_secret = admin_username, secret
    warehouse.api_url = warehouse.api_url or default_api_url(warehouse_id)
    warehouse.registered_by, warehouse.registered_at = principal.username, now_utc()

    provider = get_identity_provider()
    credentials = []
    for username, display, role in ((admin_username, f"Administrador {data.name}", ADMIN_BODEGA),
                                    (f"registrador.{suffix}", f"Registrador {data.name}", REGISTRADOR)):
        if db.get(UserEntity, username) is None:
            password = temporary_password()
            provider.create_user(db, username, display, role, warehouse_id, password, principal.username)
            credentials.append({"username": username, "role": role, "temporary_password": password})
    queued = platform_commands.send_full_catalog(db, warehouse_id)
    notify(db, ADMIN_GLOBAL, "WAREHOUSE_REGISTERED", f"Bodega {warehouse_id} registrada",
           f"{data.name} ({data.location}), NIT {data.nit}. Esperando su primer heartbeat.", INFO, warehouse_id)
    db.commit()
    logger.info("Bodega registrada", extra={"event": "warehouse_registered", "context": {
        "warehouse_id": warehouse_id, "nit": data.nit, "catalog_commands": queued, "by": principal.username}})
    return {
        "warehouse_id": warehouse_id,
        "status": "REGISTERED",
        "command_queue": f"commands.{warehouse_id}",
        "catalog_products_queued": queued,
        "initial_credentials": credentials,
        "env_file": provisioning_env(warehouse_id, secret),
        "terraform_entry": (f'{warehouse_id.lower().replace("-", "_")} = {{ wh_id = "{warehouse_id}", '
                            f'api_port = {8000 + int(warehouse_id[3:])}, web_port = {8080 + int(warehouse_id[3:])}, '
                            f'rabbit_mgmt_port = {15672 + int(warehouse_id[3:])} }}'),
    }


@router.get("/warehouses", summary="Bodegas registradas con sus datos")
def registered_warehouses(db: Session = Depends(get_db), _: Principal = Depends(admin_only)):
    rows = db.query(WarehouseEntity).order_by(WarehouseEntity.warehouse_id).all()
    return [{"warehouse_id": w.warehouse_id, "nit": w.nit, "name": w.name, "location": w.location, "address": w.address,
             "description": w.description, "capacity_units": w.capacity_units, "admin_username": w.admin_username,
             "registered_at": w.registered_at, "registered_by": w.registered_by} for w in rows]


@router.get("/users", summary="Usuarios y sus roles")
def list_users(db: Session = Depends(get_db), _: Principal = Depends(admin_only)):
    return [_user_view(u) for u in db.query(UserEntity).order_by(UserEntity.role, UserEntity.username).all()]


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Crear un usuario (por ejemplo, credenciales para un líder)")
def create_user(data: UserCreate, db: Session = Depends(get_db), principal: Principal = Depends(admin_only)):
    password = temporary_password()
    try:
        user = get_identity_provider().create_user(db, data.username, data.display_name, data.role, data.warehouse_id,
                                                   password, principal.username)
    except IdentityError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    db.commit()
    logger.info("Usuario creado", extra={"event": "user_created", "context": {
        "username": user.username, "role": user.role, "by": principal.username}})
    return {**_user_view(user), "temporary_password": password}


@router.post("/users/{username}/reset-password", summary="Generar una contraseña temporal nueva")
def reset_password(username: str, db: Session = Depends(get_db), principal: Principal = Depends(admin_only)):
    if db.get(UserEntity, username) is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    password = temporary_password()
    try:
        get_identity_provider().set_password(db, username, password, temporary=True)
    except IdentityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    db.commit()
    logger.info("Contraseña temporal generada", extra={"event": "password_reset", "context": {"username": username, "by": principal.username}})
    return {"username": username, "temporary_password": password}


@router.post("/users/{username}/active", summary="Activar o desactivar un usuario")
def set_active(username: str, active: bool, db: Session = Depends(get_db), principal: Principal = Depends(admin_only)):
    user = db.get(UserEntity, username)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if user.username == principal.username and not active:
        raise HTTPException(status_code=409, detail="No puedes desactivar tu propio usuario")
    user.is_active = active
    db.commit()
    return _user_view(user)


@router.get("/settings", summary="Configuración de identidad en uso")
def identity_settings(_: Principal = Depends(admin_only)):
    provider = get_identity_provider()
    directory = {"url": settings.LDAP_URL, "base_dn": settings.LDAP_BASE_DN} if provider.name == "ldap" else None
    return {"identity_provider": provider.name, "manages_users": provider.manages_users, "directory": directory,
            "token_ttl_minutes": settings.AUTH_TOKEN_TTL_MINUTES}
