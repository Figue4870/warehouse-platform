"""Reglas de alerta por tipo de sensor. Se evalúan en orden de sequence_number; una lectura atrasada no las mueve.

Cada regla abre una alerta tras `confirm` lecturas seguidas fuera de rango (evita alertas por un pico aislado) y
la cierra cuando el sensor vuelve a la normalidad. El movimiento nocturno se considera vigente hasta pasar
`IOT_MOTION_QUIET_SECONDS` sin movimiento, para no abrir y cerrar una alerta con cada detección.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")


def _env(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str
    confirm: int
    message: str


TEMPERATURE = Rule("TEMPERATURE_OUT_OF_RANGE", "CRITICAL", 3, "Temperatura fuera del rango permitido")
HUMIDITY = Rule("HUMIDITY_HIGH", "WARNING", 3, "Humedad alta: riesgo para la mercancía")
DOOR = Rule("DOOR_OPEN_TOO_LONG", "WARNING", 1, "Puerta abierta demasiado tiempo")
MOTION = Rule("MOTION_AFTER_HOURS", "CRITICAL", 1, "Movimiento fuera del horario de operación")
ENERGY = Rule("ENERGY_SPIKE", "WARNING", 2, "Consumo de energía anormal")


def limits() -> dict:
    return {"temp_min": _env("IOT_TEMP_MIN", 10), "temp_max": _env("IOT_TEMP_MAX", 30), "humidity_max": _env("IOT_HUMIDITY_MAX", 75),
            "door_open_seconds": _env("IOT_DOOR_OPEN_SECONDS", 120), "energy_max": _env("IOT_ENERGY_MAX_KW", 15),
            "night_from": int(_env("IOT_NIGHT_FROM_HOUR", 22)), "night_to": int(_env("IOT_NIGHT_TO_HOUR", 5)),
            "motion_quiet_seconds": _env("IOT_MOTION_QUIET_SECONDS", 300)}


def after_hours(moment: datetime, night_from: int, night_to: int) -> bool:
    hour = moment.astimezone(BOGOTA).hour
    return hour >= night_from or hour < night_to


def evaluate(sensor_type: str, value: float, measured_at: datetime, state: dict) -> tuple[Optional[Rule], bool, str, dict]:
    """(regla, anormal, detalle, nuevo estado). El estado guarda las lecturas anormales seguidas y desde cuándo."""
    lim = limits()
    state = dict(state or {})
    if sensor_type == "temperature":
        rule, abnormal = TEMPERATURE, not lim["temp_min"] <= value <= lim["temp_max"]
        detail = f"{value:.1f} °C (permitido {lim['temp_min']:g}–{lim['temp_max']:g} °C)"
    elif sensor_type == "humidity":
        rule, abnormal = HUMIDITY, value > lim["humidity_max"]
        detail = f"{value:.0f} % (máximo {lim['humidity_max']:g} %)"
    elif sensor_type == "energy":
        rule, abnormal = ENERGY, value > lim["energy_max"]
        detail = f"{value:.1f} kW (máximo {lim['energy_max']:g} kW)"
    elif sensor_type == "motion":
        rule, night = MOTION, after_hours(measured_at, lim["night_from"], lim["night_to"])
        if value >= 1 and night:
            state["last_motion"] = measured_at.isoformat()
        last = datetime.fromisoformat(state["last_motion"]) if state.get("last_motion") else None
        abnormal = night and last is not None and (measured_at - last).total_seconds() < lim["motion_quiet_seconds"]
        detail = f"detectado a las {measured_at.astimezone(BOGOTA):%H:%M} (hora de Bogotá)"
    elif sensor_type == "door":
        rule = DOOR
        if value >= 1:
            opened = datetime.fromisoformat(state["open_since"]) if state.get("open_since") else measured_at
            state["open_since"] = opened.isoformat()
            seconds = (measured_at - opened).total_seconds()
            abnormal = seconds > lim["door_open_seconds"]
            detail = f"abierta hace {seconds:.0f} s (máximo {lim['door_open_seconds']:g} s)"
        else:
            state.pop("open_since", None)
            abnormal, detail = False, "cerrada"
    else:
        return None, False, "", state
    state["streak"] = state.get("streak", 0) + 1 if abnormal else 0
    return rule, abnormal, detail, state
