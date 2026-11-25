"""Heartbeat reto-respuesta cifrado entre la central y las bodegas.

La central envía un reto y la bodega lo devuelve junto con su estado. Cada mensaje viaja en un sobre
{v, kind, warehouse_id, nonce, ciphertext} cifrado con AES-256-GCM, con una clave por bodega derivada
con HKDF-SHA256. El tipo de mensaje y la bodega van como datos asociados (AAD), de modo que un mensaje
no sirve como otro tipo ni para otra bodega. Se rechazan los mensajes con más de MAX_AGE_SECONDS y los
retos ya respondidos.
"""

import base64
import binascii
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1
CHALLENGE = "challenge"
RESPONSE = "response"
MAX_AGE_SECONDS = 60
MIN_SECRET_LENGTH = 16


class HeartbeatError(Exception):
    """Mensaje de heartbeat no válido. `reason` resume la causa para los logs."""

    reason = "invalid"


class AuthenticationError(HeartbeatError):
    """Clave distinta, mensaje alterado o con un formato inesperado."""

    reason = "authentication_failed"


class StaleMessageError(HeartbeatError):
    """Mensaje fuera de la ventana de tiempo permitida."""

    reason = "stale"


class ReplayError(HeartbeatError):
    """Reto repetido o respuesta que no corresponde al reto enviado."""

    reason = "replay"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def derive_key(secret: str) -> bytes:
    """Clave AES de 256 bits a partir del secreto compartido de una bodega."""
    if not secret or len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"La clave del heartbeat debe tener al menos {MIN_SECRET_LENGTH} caracteres")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"warehouse-platform", info=b"secure-heartbeat-v1")
    return hkdf.derive(secret.encode("utf-8"))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def _associated_data(kind: str, warehouse_id: str) -> bytes:
    return f"v{VERSION}|{kind}|{warehouse_id}".encode("utf-8")


def seal(key: bytes, kind: str, warehouse_id: str, payload: dict) -> dict:
    """Cifra `payload` y devuelve el sobre que viaja por la red (nonce aleatorio de 96 bits por mensaje)."""
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _associated_data(kind, warehouse_id))
    return {"v": VERSION, "kind": kind, "warehouse_id": warehouse_id, "nonce": _b64(nonce), "ciphertext": _b64(ciphertext)}


def open_sealed(key: bytes, kind: str, warehouse_id: str, envelope: dict) -> dict:
    """Descifra y autentica un sobre del tipo y la bodega esperados. Devuelve su contenido."""
    if not isinstance(envelope, dict):
        raise AuthenticationError("El mensaje no tiene el formato esperado")
    if envelope.get("v") != VERSION or envelope.get("kind") != kind or envelope.get("warehouse_id") != warehouse_id:
        raise AuthenticationError("El mensaje no es del tipo o de la bodega esperados")
    try:
        nonce = _unb64(envelope["nonce"])
        ciphertext = _unb64(envelope["ciphertext"])
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _associated_data(kind, warehouse_id))
        payload = json.loads(plaintext)
    except (InvalidTag, KeyError, TypeError, ValueError, binascii.Error) as error:
        raise AuthenticationError("No se pudo descifrar: clave equivocada o mensaje alterado") from error
    if not isinstance(payload, dict):
        raise AuthenticationError("El contenido descifrado no tiene el formato esperado")
    return payload


def check_fresh(timestamp: str, now: datetime | None = None, max_age_seconds: int = MAX_AGE_SECONDS) -> None:
    """Rechaza marcas de tiempo a más de `max_age_seconds` de la hora actual, hacia atrás o hacia adelante."""
    now = now or utc_now()
    try:
        moment = datetime.fromisoformat(str(timestamp))
    except ValueError as error:
        raise StaleMessageError("El mensaje no trae una hora válida") from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if abs((now - moment).total_seconds()) > max_age_seconds:
        raise StaleMessageError(f"El mensaje tiene más de {max_age_seconds} s")


class ReplayGuard:
    """Recuerda los retos respondidos por este proceso durante MAX_AGE_SECONDS para rechazar repeticiones."""

    def __init__(self, max_age_seconds: int = MAX_AGE_SECONDS, clock=time.monotonic):
        self.max_age_seconds = max_age_seconds
        self.clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_remember(self, challenge_id: str) -> None:
        now = self.clock()
        with self._lock:
            for old in [c for c, at in self._seen.items() if now - at > self.max_age_seconds]:
                del self._seen[old]
            if challenge_id in self._seen:
                raise ReplayError("Ese reto ya se respondió")
            self._seen[challenge_id] = now


def new_challenge(warehouse_id: str, now: datetime | None = None) -> dict:
    """Reto nuevo: valor aleatorio, bodega destino y hora de emisión."""
    return {"challenge": secrets.token_urlsafe(24), "warehouse_id": warehouse_id, "issued_at": (now or utc_now()).isoformat()}


def verify_response(key: bytes, warehouse_id: str, challenge: dict, envelope: dict, now: datetime | None = None) -> dict:
    """Lado central: valida la respuesta a `challenge` y devuelve el estado que reportó la bodega."""
    payload = open_sealed(key, RESPONSE, warehouse_id, envelope)
    if payload.get("challenge") != challenge["challenge"]:
        raise ReplayError("La respuesta no corresponde al reto enviado")
    if payload.get("warehouse_id") != warehouse_id:
        raise AuthenticationError("La respuesta dice venir de otra bodega")
    check_fresh(payload.get("responded_at"), now)
    status = payload.get("status")
    return status if isinstance(status, dict) else {}


def answer_challenge(key: bytes, warehouse_id: str, envelope: dict, status: dict, guard: ReplayGuard,
                     now: datetime | None = None) -> dict:
    """Lado bodega: valida el reto de la central y devuelve la respuesta cifrada con `status`."""
    payload = open_sealed(key, CHALLENGE, warehouse_id, envelope)
    if payload.get("warehouse_id") != warehouse_id or not payload.get("challenge"):
        raise AuthenticationError("El reto no es para esta bodega")
    check_fresh(payload.get("issued_at"), now)
    guard.check_and_remember(str(payload["challenge"]))
    return seal(key, RESPONSE, warehouse_id, {
        "challenge": payload["challenge"],
        "warehouse_id": warehouse_id,
        "responded_at": (now or utc_now()).isoformat(),
        "status": status,
    })


def parse_secrets(text: str) -> dict[str, str]:
    """Convierte "WH-001=clave,WH-002=clave" en {"WH-001": "clave", "WH-002": "clave"}."""
    result = {}
    for item in filter(None, (part.strip() for part in (text or "").split(","))):
        if "=" not in item:
            raise ValueError(f"Entrada sin '=' en la lista de claves: {item[:10]}...")
        warehouse_id, secret = item.split("=", 1)
        result[warehouse_id.strip()] = secret.strip()
    return result
