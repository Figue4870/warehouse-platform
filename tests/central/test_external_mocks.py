"""Sistemas externos simulados: contrato que la central espera de Proveedores y Servientrega."""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_path = Path(__file__).resolve().parents[2] / "services" / "external-mocks" / "mocks.py"
_spec = importlib.util.spec_from_file_location("external_mocks", _path)
mocks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mocks)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(mocks.control["suppliers"], "latency_ms", 0)
    monkeypatch.setitem(mocks.control["servientrega"], "deliver_after_seconds", 3600)
    monkeypatch.setitem(mocks.control["servientrega"], "webhook", False)
    with TestClient(mocks.app) as test_client:
        yield test_client
    mocks.control["suppliers"]["down"] = mocks.control["servientrega"]["down"] = False


def test_proveedores_consulta_y_caida(client):
    assert client.get("/proveedores/suppliers/900123456-7").json()["status"] == "ACTIVE"
    assert client.get("/proveedores/suppliers/860999888-0").json()["status"] == "SUSPENDED"
    assert client.get("/proveedores/suppliers/111").status_code == 404
    client.post("/_control", json={"suppliers": {"down": True}})
    assert client.get("/proveedores/suppliers/900123456-7").status_code == 503


def test_servientrega_es_idempotente_por_referencia(client):
    body = {"reference": "TR-000777", "origin": {}, "destination": {}, "items": [{"sku": "A", "quantity": 1}]}
    first = client.post("/servientrega/shipments", json=body, headers={"Idempotency-Key": "TR-000777"})
    again = client.post("/servientrega/shipments", json=body, headers={"Idempotency-Key": "TR-000777"})
    assert first.status_code == 202 and first.json()["tracking_number"] == again.json()["tracking_number"]
    assert client.get(f"/servientrega/shipments/{first.json()['tracking_number']}").json()["status"] == "CREATED"
