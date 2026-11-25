"""Heartbeat de punta a punta sin Docker: la central le pregunta a la API de la bodega en memoria."""

import pytest
from fastapi.testclient import TestClient
from platform_common import secure_heartbeat as hb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.all_models  # noqa: F401
from app.config import settings as warehouse_settings
from app.database import Base as WarehouseBase
from app.database import get_db as warehouse_get_db
from app.modules.sync import heartbeat as warehouse_heartbeat
from central import heartbeat as central_heartbeat
from central.heartbeat import OK, REJECTED, HeartbeatClient, poll_warehouse
from central.models import WarehouseEntity
from main import app as warehouse_app

SECRET = "clave-compartida-de-punta-a-punta-01"


@pytest.fixture
def warehouse_transport(monkeypatch):
    """API de la bodega (con SQLite en memoria) expuesta como transporte de httpx para la central."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    WarehouseBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)

    def _get_db():
        db = session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(warehouse_settings, "HEARTBEAT_SECRET", SECRET)
    monkeypatch.setattr(warehouse_heartbeat, "replay_guard", hb.ReplayGuard())
    central_heartbeat._key.cache_clear()
    warehouse_app.dependency_overrides[warehouse_get_db] = _get_db
    with TestClient(warehouse_app) as client:
        yield client._transport
    warehouse_app.dependency_overrides.pop(warehouse_get_db, None)
    engine.dispose()


def test_la_central_real_pregunta_y_la_bodega_real_responde(central_sf, warehouse_transport):
    warehouse_id = warehouse_settings.WAREHOUSE_ID
    result = poll_warehouse(central_sf, warehouse_id, "http://testserver", SECRET, HeartbeatClient(transport=warehouse_transport))
    assert result.outcome == OK and result.instance == warehouse_settings.INSTANCE_NAME
    with central_sf() as db:
        assert db.get(WarehouseEntity, warehouse_id).last_heartbeat_at is not None


def test_si_las_claves_no_coinciden_no_hay_heartbeat(central_sf, warehouse_transport):
    warehouse_id = warehouse_settings.WAREHOUSE_ID
    result = poll_warehouse(central_sf, warehouse_id, "http://testserver", "otra-clave-distinta-de-la-bodega-99",
                            HeartbeatClient(transport=warehouse_transport))
    assert result.outcome == REJECTED and "las claves no coinciden" in result.detail
    with central_sf() as db:
        warehouse = db.get(WarehouseEntity, warehouse_id)
        assert warehouse.last_heartbeat_at is None and warehouse.heartbeat_rejections == 1
