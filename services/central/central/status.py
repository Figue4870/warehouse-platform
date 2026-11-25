"""Qué tan confiable es la información de cada bodega en este momento."""

from datetime import datetime

from central.config import settings
from central.models import WarehouseEntity
from central.projection import as_aware

ONLINE, OFFLINE, UNKNOWN = "ONLINE", "OFFLINE", "UNKNOWN"


def warehouse_status(warehouse: WarehouseEntity, now: datetime) -> dict:
    heartbeat = as_aware(warehouse.last_heartbeat_at)
    seconds_since_contact = round((now - heartbeat).total_seconds(), 1) if heartbeat else None
    if heartbeat is None:
        status = UNKNOWN
    elif seconds_since_contact <= settings.ONLINE_AFTER_SECONDS:
        status = ONLINE
    else:
        status = OFFLINE
    lag = max(warehouse.reported_sequence or 0, warehouse.max_received_sequence or 0) - (warehouse.watermark_sequence or 0)
    stale = status != ONLINE or lag > 0
    error = warehouse.last_heartbeat_error
    if status == OFFLINE:
        note = f"Sin respuesta válida hace {int(seconds_since_contact)} s: sus datos pueden estar desactualizados"
        if error:
            note += f" ({error})"
    elif status == UNKNOWN:
        note = f"Todavía sin respuesta válida: {error}" if error else "La central aún no le ha preguntado"
    elif error:
        note = f"La última pregunta falló: {error}"
    elif lag > 0:
        note = f"Faltan {lag} evento(s) por recibir (se recuperarán solos)"
    else:
        note = "Al día"
    return {
        "warehouse_id": warehouse.warehouse_id,
        "name": warehouse.name,
        "location": warehouse.location,
        "status": status,
        "stale": stale,
        "note": note,
        "seconds_since_contact": seconds_since_contact,
        "last_heartbeat_at": heartbeat,
        # Detalle del heartbeat cifrado (lo muestra el tablero)
        "heartbeat": {
            "authenticated": status == ONLINE,
            "last_attempt_at": as_aware(warehouse.last_heartbeat_attempt_at),
            "error": error,
            "failures_in_a_row": warehouse.heartbeat_failures or 0,
            "rejected_total": warehouse.heartbeat_rejections or 0,
            "answered_by": warehouse.last_heartbeat_instance,
        },
        "last_event_at": as_aware(warehouse.last_event_at),
        "reported_sequence": warehouse.reported_sequence,
        "watermark_sequence": warehouse.watermark_sequence,
        "max_received_sequence": warehouse.max_received_sequence,
        "events_missing": max(lag, 0),
        "outbox_pending": warehouse.outbox_pending,
        "api_url": warehouse.api_url,
        "last_sync": {
            "at": as_aware(warehouse.last_sync_at),
            "kind": warehouse.last_sync_kind,
            "status": warehouse.last_sync_status,
            "error": warehouse.last_sync_error,
        },
    }
