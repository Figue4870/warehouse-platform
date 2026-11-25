"""Monitor de heartbeat: la central le envía a cada bodega un reto cifrado y registra si respondió bien."""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

import httpx
from platform_common import secure_heartbeat as hb
from sqlalchemy.orm import sessionmaker

from central.config import default_api_url, settings, warehouse_registry
from central.models import WarehouseEntity
from central.platform_commands import send_full_catalog
from central.projection import now_utc, record_heartbeat, record_heartbeat_failure

logger = logging.getLogger("central.heartbeat")

# ok: respuesta auténtica; unreachable: sin respuesta; rejected: respuesta no auténtica o reto rechazado;
# no_secret: la bodega no tiene clave configurada
OK, UNREACHABLE, REJECTED, NO_SECRET = "ok", "unreachable", "rejected", "no_secret"


@dataclass
class PollResult:
    """Resultado de preguntarle a una bodega."""

    warehouse_id: str
    outcome: str
    detail: str = ""
    instance: str | None = None


class HeartbeatClient:
    """Envía el reto cifrado a POST /sync/heartbeat de una bodega."""

    def __init__(self, timeout: float = settings.HEARTBEAT_TIMEOUT_SECONDS, transport=None):
        self.timeout = timeout
        self.transport = transport  # en las pruebas se reemplaza por uno falso

    def send(self, api_url: str, envelope: dict) -> httpx.Response:
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            return client.post(f"{api_url}/sync/heartbeat", json=envelope)


def full_registry(session_factory: sessionmaker) -> dict:
    """Bodegas de la configuración más las registradas por el administrador global (con su clave en la base)."""
    registry = warehouse_registry()
    with session_factory() as db:
        rows = db.query(WarehouseEntity).filter(WarehouseEntity.heartbeat_secret.isnot(None)).all()
        for row in rows:
            entry = registry.setdefault(row.warehouse_id, {"api_url": row.api_url or default_api_url(row.warehouse_id),
                                                           "secret": None})
            entry["secret"] = entry.get("secret") or row.heartbeat_secret
    return registry


@lru_cache(maxsize=256)
def _key(secret: str) -> bytes:
    return hb.derive_key(secret)


def ask(warehouse_id: str, api_url: str, secret: str, client: HeartbeatClient) -> tuple[PollResult, dict | None]:
    """Envía un reto y verifica la respuesta. Devuelve el resultado y, si fue auténtica, el estado de la bodega."""
    try:
        key = _key(secret)
    except ValueError as error:
        return PollResult(warehouse_id, NO_SECRET, f"Clave de heartbeat inválida: {error}"), None
    challenge = hb.new_challenge(warehouse_id)
    envelope = hb.seal(key, hb.CHALLENGE, warehouse_id, challenge)
    try:
        response = client.send(api_url, envelope)
    except httpx.HTTPError as error:
        return PollResult(warehouse_id, UNREACHABLE, f"Sin respuesta: {type(error).__name__}"), None
    # 401: la bodega no pudo autenticar el reto; 409: dice que ese reto ya lo había respondido
    if response.status_code in (401, 409):
        return PollResult(warehouse_id, REJECTED, f"La bodega rechazó el reto (HTTP {response.status_code}): las claves no coinciden"), None
    if response.status_code >= 400:
        return PollResult(warehouse_id, UNREACHABLE, f"La bodega respondió HTTP {response.status_code}"), None
    try:
        status = hb.verify_response(key, warehouse_id, challenge, response.json())
    except hb.HeartbeatError as error:
        return PollResult(warehouse_id, REJECTED, f"Respuesta no auténtica ({error.reason}): {error}"), None
    except ValueError:
        return PollResult(warehouse_id, REJECTED, "Respuesta no auténtica (no es un mensaje cifrado)"), None
    return PollResult(warehouse_id, OK, instance=status.get("instance")), status


def ask_safely(warehouse_id: str, api_url: str, secret: str | None, client: HeartbeatClient) -> tuple[PollResult, dict | None]:
    """Como ask(), pero sin consultar a las bodegas que no tienen clave: nunca se pregunta sin cifrar."""
    if not secret:
        return PollResult(warehouse_id, NO_SECRET, "Sin clave de heartbeat para esta bodega (WAREHOUSE_HEARTBEAT_SECRETS)"), None
    return ask(warehouse_id, api_url, secret, client)


def record(session_factory: sessionmaker, api_url: str, result: PollResult, status: dict | None) -> PollResult:
    """Guarda el resultado en el modelo de lectura y lo registra en el log."""
    warehouse_id = result.warehouse_id
    with session_factory() as db:
        if result.outcome == OK:
            record_heartbeat(db, warehouse_id, status, api_url=api_url)
            warehouse = db.get(WarehouseEntity, warehouse_id)
            if warehouse.catalog_sent_at is None:
                # Primera respuesta válida: la bodega recibe el catálogo universal completo por su cola de comandos
                send_full_catalog(db, warehouse_id)
                warehouse.catalog_sent_at = now_utc()
        else:
            record_heartbeat_failure(db, warehouse_id, result.detail, rejected=result.outcome == REJECTED, api_url=api_url)
        db.commit()
    if result.outcome == REJECTED:
        logger.warning("Heartbeat rechazado: respuesta no auténtica", extra={"event": "heartbeat_rejected", "context": {
            "warehouse_id": warehouse_id, "api_url": api_url, "detail": result.detail}})
    elif result.outcome in (UNREACHABLE, NO_SECRET):
        logger.info("La bodega no respondió el heartbeat", extra={"event": "heartbeat_failed", "context": {
            "warehouse_id": warehouse_id, "api_url": api_url, "detail": result.detail}})
    return result


def poll_warehouse(session_factory: sessionmaker, warehouse_id: str, api_url: str, secret: str | None,
                   client: HeartbeatClient) -> PollResult:
    """Pregunta a una bodega y guarda el resultado."""
    result, status = ask_safely(warehouse_id, api_url, secret, client)
    return record(session_factory, api_url, result, status)


def poll_once(session_factory: sessionmaker, client: HeartbeatClient, registry: dict | None = None) -> list[PollResult]:
    """Una vuelta del monitor sobre todas las bodegas del registro."""
    registry = warehouse_registry() if registry is None else registry
    if not registry:
        return []
    items = sorted(registry.items())
    # Las consultas HTTP van en paralelo (una bodega lenta no retrasa a las demás); las escrituras, en serie
    with ThreadPoolExecutor(max_workers=min(16, len(items))) as pool:
        answers = list(pool.map(lambda item: ask_safely(item[0], item[1]["api_url"], item[1].get("secret"), client), items))
    return [record(session_factory, entry["api_url"], result, status) for (_, entry), (result, status) in zip(items, answers)]
