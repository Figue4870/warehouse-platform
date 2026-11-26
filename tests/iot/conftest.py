"""Pruebas de la extensión IoT (Fase 12): SQLite en memoria, sin broker ni Docker."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import itertools  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from iot_service import models  # noqa: E402,F401
from iot_service.database import Base  # noqa: E402
from iot_service.readings import Reading  # noqa: E402


@pytest.fixture
def iot_sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


class SensorFactory:
    """Lecturas de un sensor con secuencia creciente y reloj controlado."""

    def __init__(self, sensor_type="temperature", sensor_id="TEMP-001-001", warehouse_id="WH-001"):
        self.sensor_type, self.sensor_id, self.warehouse_id = sensor_type, sensor_id, warehouse_id
        self.seq = itertools.count(1)
        self.clock = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)

    def __call__(self, value, sequence=None, seconds=2):
        self.clock += timedelta(seconds=seconds)
        return Reading(event_id=f"EVT-{uuid.uuid4().hex[:12]}", sequence_number=sequence if sequence is not None else next(self.seq),
                       warehouse_id=self.warehouse_id, sensor_id=self.sensor_id, sensor_type=self.sensor_type, value=value,
                       timestamp=self.clock)


@pytest.fixture
def sensor():
    return SensorFactory
