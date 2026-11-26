"""Fase 12: lo que la central muestra de los sensores (consulta síncrona al servicio IoT, que es independiente)."""

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from platform_common.auth import ADMIN_BODEGA, ADMIN_GLOBAL, LIDER, Principal

from central.config import settings
from central.security import require

router = APIRouter(prefix="/iot", tags=["IoT"])


def _get(path: str, params: dict) -> dict | list:
    try:
        response = httpx.get(f"{settings.IOT_API_URL.rstrip('/')}{path}", params=params, timeout=settings.EXTERNAL_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError as error:
        # Si la extensión IoT no está, el resto de la central sigue igual
        raise HTTPException(status_code=503, detail=f"El servicio IoT no responde ({type(error).__name__})") from error
    return response.json()


@router.get("/summary", summary="Sensores por bodega, rendimiento, latencia y alertas abiertas")
def summary(minutes: int = Query(5, ge=1, le=1440), principal: Principal = Depends(require(ADMIN_GLOBAL, LIDER, ADMIN_BODEGA))):
    data = _get("/iot/summary", {"minutes": minutes})
    if principal.role == ADMIN_BODEGA:
        data["warehouses"] = [w for w in data["warehouses"] if w["warehouse_id"] == principal.warehouse_id]
    return data


@router.get("/alerts", summary="Alertas de sensores")
def alerts(open_only: bool = Query(True, alias="open"), warehouse_id: Optional[str] = None,
           principal: Principal = Depends(require(ADMIN_GLOBAL, LIDER, ADMIN_BODEGA))):
    if principal.role == ADMIN_BODEGA:
        warehouse_id = principal.warehouse_id
    return _get("/iot/alerts", {"open": open_only, **({"warehouse_id": warehouse_id} if warehouse_id else {})})
