"""Clientes de los sistemas externos.

Proveedores: consulta síncrona (la respuesta se necesita para decidir), con timeout corto.
Servientrega: integración asíncrona (se pide el envío y el estado llega después por webhook o consulta).
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional

import httpx

from central.config import settings


class ExternalUnavailable(Exception):
    """El sistema externo no respondió a tiempo o falló (se puede reintentar)."""


class ExternalRejected(Exception):
    """El sistema externo respondió que no (no se reintenta)."""


@dataclass
class Supplier:
    nit: str
    name: str
    status: str
    email: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.status == "ACTIVE"


def _client() -> httpx.Client:
    return httpx.Client(timeout=settings.EXTERNAL_TIMEOUT_SECONDS)


def normalize_nit(nit: str) -> str:
    return "".join(ch for ch in nit if ch.isdigit() or ch == "-")


def get_supplier(nit: str) -> Optional[Supplier]:
    """None si Proveedores no lo conoce. ExternalUnavailable si no responde."""
    try:
        with _client() as client:
            response = client.get(f"{settings.SUPPLIERS_API_URL}/suppliers/{normalize_nit(nit)}")
    except httpx.HTTPError as error:
        raise ExternalUnavailable(f"Proveedores no responde: {type(error).__name__}") from error
    if response.status_code == 404:
        return None
    if response.status_code >= 500 or response.status_code == 429:
        raise ExternalUnavailable(f"Proveedores respondió {response.status_code}")
    response.raise_for_status()
    data = response.json()
    return Supplier(nit=data["nit"], name=data["name"], status=data["status"], email=data.get("email"))


def create_shipment(reference: str, origin: dict, destination: dict, items: list[dict], callback_url: str | None) -> dict:
    """Pide el envío. Idempotente: la misma referencia devuelve la misma guía."""
    body = {"reference": reference, "origin": origin, "destination": destination, "items": items, "callback_url": callback_url}
    try:
        with _client() as client:
            response = client.post(f"{settings.SERVIENTREGA_API_URL}/shipments", json=body,
                                   headers={"Idempotency-Key": reference})
    except httpx.HTTPError as error:
        raise ExternalUnavailable(f"Servientrega no responde: {type(error).__name__}") from error
    if response.status_code >= 500 or response.status_code == 429:
        raise ExternalUnavailable(f"Servientrega respondió {response.status_code}")
    if response.status_code >= 400:
        raise ExternalRejected(response.text[:300])
    return response.json()


def get_shipment(tracking_number: str) -> dict:
    try:
        with _client() as client:
            response = client.get(f"{settings.SERVIENTREGA_API_URL}/shipments/{tracking_number}")
    except httpx.HTTPError as error:
        raise ExternalUnavailable(f"Servientrega no responde: {type(error).__name__}") from error
    if response.status_code >= 400:
        raise ExternalUnavailable(f"Servientrega respondió {response.status_code}")
    return response.json()


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


def valid_signature(secret: str, timestamp: str | None, received: str | None, body: bytes, now: float,
                    max_skew_seconds: int = 300) -> bool:
    """Firma HMAC de los avisos entrantes (Lambda, Servientrega). Rechaza avisos viejos (repetición)."""
    if not secret or not timestamp or not received or not timestamp.isdigit():
        return False
    if abs(now - int(timestamp)) > max_skew_seconds:
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), received)
