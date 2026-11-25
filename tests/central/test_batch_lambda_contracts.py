"""Fases 8 y 9: Batch diario reejecutable, Lambda de S3 (contratos y reporte) y contratos del Líder."""

import importlib.util
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "lambda"))

from storage_processor import handler  # noqa: E402

from central import batch, external  # noqa: E402
from central.config import settings  # noqa: E402
from central.integrations import recover_missing  # noqa: E402
from central.models import (  # noqa: E402
    BatchRunEntity,
    ContractEntity,
    MovementHistoryEntity,
    NotificationEntity,
    TransferCommandEntity,
    UniversalProductEntity,
)
from central.storage import LocalStorage  # noqa: E402

_spec = importlib.util.spec_from_file_location("generar_contrato", ROOT / "scripts" / "contratos" / "generar_contrato.py")
generar_contrato = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generar_contrato)

SECRET = "secreto-lambda-de-prueba"


def contract_pdf(nit="900123456-7", prices=(("EPP-CASCO-001", "17.500,00"),), signed="2026-11-01", until="2027-10-31"):
    return generar_contrato.build_pdf(generar_contrato.contract_lines(nit, "Seguridad Industrial del Valle", signed, until,
                                                                      list(prices)))


@pytest.fixture
def storage(tmp_path, monkeypatch):
    local = LocalStorage(str(tmp_path / "bucket"))
    monkeypatch.setattr("central.routers.contracts.storage_from_settings", lambda: local)
    monkeypatch.setattr("central.routers.batch.storage_from_settings", lambda: local)
    return local


class Objects:
    """Lo que la Lambda ve del bucket (aquí, la misma carpeta local)."""

    def __init__(self, storage):
        self.storage = storage

    def get(self, key):
        return self.storage.get(key).body

    def put(self, key, body, content_type):
        self.storage.put(key, body, content_type)


def s3_record(key, etag="abc123"):
    return {"s3": {"bucket": {"name": "bucket"}, "object": {"key": key, "eTag": etag}}}


def signed_post(client, path, payload, secret=SECRET, timestamp=None):
    body = json.dumps(payload).encode()
    timestamp = timestamp or str(int(time.time()))
    return client.post(path, content=body, headers={"Content-Type": "application/json", "X-Signature-Timestamp": timestamp,
                                                    "X-Signature": external.signature(secret, timestamp, body)})


def add_movement(db, warehouse_id, sku, m_type, quantity, after, occurred_at, price="100.00"):
    db.add(MovementHistoryEntity(event_id=f"{warehouse_id}-{sku}-{occurred_at.timestamp()}-{m_type}", warehouse_id=warehouse_id,
                                 sku=sku, type=m_type, quantity=quantity, quantity_after=after, reserved_after=0,
                                 unit_price=Decimal(price), occurred_at=occurred_at))


# Batch
@pytest.fixture
def batch_day(central_sf, apply, wh1, wh2):
    apply(wh1.heartbeat(0), wh2.heartbeat(0))
    business_date = date(2026, 11, 20)
    start, _ = batch.day_window(business_date)
    with central_sf() as db:
        add_movement(db, "WH-001", "P-1", "RECEIPT", 50, 50, start + timedelta(hours=9), "60.00")
        add_movement(db, "WH-001", "P-1", "SHIPMENT", 20, 30, start + timedelta(hours=15), "100.00")
        add_movement(db, "WH-002", "P-1", "SHIPMENT", 5, 0, start + timedelta(hours=22, minutes=59), "110.00")
        add_movement(db, "WH-002", "P-1", "RECEIPT", 99, 99, start + timedelta(days=1, minutes=1))  # día siguiente
        db.commit()
    return business_date


def test_batch_procesa_todas_las_bodegas_y_guarda_en_s3(central_sf, storage, batch_day):
    with central_sf() as db:
        run_id = batch.request_run(db, batch_day, batch.MANUAL, "lider").id
        db.commit()
    assert batch.claim_next(central_sf) == run_id
    assert batch.execute(central_sf, storage, run_id) in (batch.SUCCEEDED, batch.PARTIAL)

    keys = set(storage.list(""))
    assert {"warehouses/WH-001/2026/11/inventory-2026-11-20.json", "warehouses/WH-002/2026/11/inventory-2026-11-20.json",
            "reports/daily/2026/11/2026-11-20.json", "reports/daily/2026/11/2026-11-20.csv",
            "audit/2026/11/20/events.jsonl"} <= keys
    report = json.loads(storage.get("reports/daily/2026/11/2026-11-20.json").body)
    assert report["totals"]["movements"] == 3                 # el movimiento del día siguiente no entra
    assert report["totals"]["sales_value"] == "2550.00"       # 20 x 100 + 5 x 110
    wh1 = json.loads(storage.get("warehouses/WH-001/2026/11/inventory-2026-11-20.json").body)
    assert wh1["purchases"] == {"units": 50, "value": "3000.00"}
    assert wh1["closing_stock"][0]["quantity"] == 30          # saldo al cierre del día, no el actual
    with central_sf() as db:
        run = db.get(BatchRunEntity, run_id)
        assert run.finished_at and run.movements_processed == 3 and len(run.outputs) == 5


def test_batch_es_reejecutable_con_las_mismas_llaves(central_sf, storage, batch_day):
    outputs = []
    for _ in range(2):
        with central_sf() as db:
            run_id = batch.request_run(db, batch_day, batch.MANUAL).id
            db.commit()
        batch.claim_next(central_sf)
        batch.execute(central_sf, storage, run_id)
        with central_sf() as db:
            outputs.append(db.get(BatchRunEntity, run_id).outputs)
    assert outputs[0] == outputs[1]
    with central_sf() as db:
        assert [r.attempt for r in db.query(BatchRunEntity).order_by(BatchRunEntity.id)] == [1, 2]


def test_programacion_a_las_23_y_recuperacion_del_dia_anterior(central_sf):
    bogota_2230 = datetime(2026, 11, 21, 22, 30, tzinfo=batch.zone())
    created = batch.schedule_due(central_sf, bogota_2230)
    with central_sf() as db:
        runs = [(r.business_date, r.trigger) for r in db.query(BatchRunEntity).order_by(BatchRunEntity.id)]
    assert len(created) == 1 and runs == [(date(2026, 11, 20), batch.CATCH_UP)]   # antes de las 23:00 solo el de ayer

    created = batch.schedule_due(central_sf, bogota_2230 + timedelta(hours=1))
    assert len(created) == 1                                                        # 23:30: el de hoy
    assert batch.schedule_due(central_sf, bogota_2230 + timedelta(hours=1, minutes=5)) == []   # no se repite


def test_una_fecha_fallida_se_reintenta_solo_hasta_el_limite(central_sf, monkeypatch):
    class Broken:
        def put(self, *a, **k):
            raise ConnectionError("S3 no responde")

    now = datetime(2026, 11, 21, 10, 0, tzinfo=batch.zone())
    for _ in range(settings.BATCH_MAX_ATTEMPTS + 2):
        batch.schedule_due(central_sf, now)
        while (run_id := batch.claim_next(central_sf)) is not None:
            assert batch.execute(central_sf, Broken(), run_id) == batch.FAILED
    with central_sf() as db:
        assert db.query(BatchRunEntity).count() == settings.BATCH_MAX_ATTEMPTS
        assert db.query(NotificationEntity).filter(NotificationEntity.kind == "BATCH_FAILED").count() == settings.BATCH_MAX_ATTEMPTS


def test_api_batch_manual_y_descarga(central_client, central_sf, storage, batch_day):
    response = central_client.post("/batch/runs", json={"business_date": batch_day.isoformat()})
    assert response.status_code == 202 and response.json()["status"] == "REQUESTED"
    assert central_client.post("/batch/runs", json={"business_date": batch_day.isoformat()}).status_code == 409
    assert central_client.post("/batch/runs", json={"business_date": "2999-01-01"}).status_code == 422
    batch.execute(central_sf, storage, batch.claim_next(central_sf))
    run = central_client.get("/batch/runs").json()["items"][0]
    csv_key = next(k for k in run["outputs"] if k.endswith(".csv"))
    download = central_client.get("/batch/files", params={"key": csv_key})
    assert download.status_code == 200 and download.text.startswith("business_date,warehouse_id")
    assert central_client.get("/batch/files", params={"key": "contracts/CT-000001/document.pdf"}).status_code == 404


# Lambda
def test_lambda_extrae_la_metadata_del_contrato(storage):
    storage.put("contracts/CT-000001/document.pdf", contract_pdf(), "application/pdf")
    sent = []
    result = handler.process_record(s3_record("contracts/CT-000001/document.pdf"), Objects(storage), sent.append)
    assert result["outcome"] == "OK" and result["event_id"] == "contracts/CT-000001/document.pdf#abc123"
    metadata = json.loads(storage.get("contracts/CT-000001/metadata.json").body)
    assert metadata["status"] == "EXTRACTED" and metadata["pages"] == 1
    assert metadata["extracted"] == {"supplier_name": "Seguridad Industrial del Valle", "supplier_nit": "900123456-7",
                                     "signed_on": "2026-11-01", "valid_until": "2027-10-31",
                                     "agreed_prices": [{"sku": "EPP-CASCO-001", "price": "17500.00"}]}
    assert sent and sent[0]["kind"] == "CONTRACT_PROCESSED"


def test_lambda_rechaza_un_pdf_incompleto(storage):
    storage.put("contracts/CT-000002/document.pdf", contract_pdf()[:300], "application/pdf")
    result = handler.process_record(s3_record("contracts/CT-000002/document.pdf"), Objects(storage), lambda e: True)
    assert result["outcome"] == "REJECTED" and "incompleto" in result["detail"]["errors"][0]


def test_lambda_ignora_lo_que_no_le_corresponde(storage):
    storage.put("contracts/CT-000001/metadata.json", b"{}", "application/json")
    assert handler.process_record(s3_record("contracts/CT-000001/metadata.json"), Objects(storage), lambda e: True)["skipped"]


def test_lambda_verifica_el_reporte_diario(storage):
    report = {"business_date": "2026-11-20", "stale_warehouses": [], "totals": {"movements": 3, "purchases_value": "0.00",
              "sales_value": "300.00"}, "warehouses": [
                  {"warehouse_id": "WH-001", "movements": 2, "sales": {"value": "200.00"}, "out_of_stock": 1},
                  {"warehouse_id": "WH-002", "movements": 1, "sales": {"value": "100.00"}, "out_of_stock": 0}]}
    storage.put("reports/daily/2026/11/2026-11-20.json", json.dumps(report).encode(), "application/json")
    ok = handler.process_record(s3_record("reports/daily/2026/11/2026-11-20.json"), Objects(storage), lambda e: True)
    assert ok["outcome"] == "OK"
    summary = json.loads(storage.get("reports/summaries/2026/11/2026-11-20.json").body)
    assert summary["top_sales"][0]["warehouse_id"] == "WH-001" and summary["out_of_stock_skus"] == 1

    report["totals"]["sales_value"] = "999.00"
    storage.put("reports/daily/2026/11/2026-11-20.json", json.dumps(report).encode(), "application/json")
    bad = handler.process_record(s3_record("reports/daily/2026/11/2026-11-20.json", "v2"), Objects(storage), lambda e: True)
    assert bad["outcome"] == "REJECTED" and "999.00" in bad["detail"]["errors"][0]


def test_firma_de_la_lambda_es_la_que_valida_la_central():
    body, timestamp = b'{"a": 1}', str(int(time.time()))
    assert external.valid_signature(SECRET, timestamp, handler.sign(SECRET, timestamp, body), body, time.time())
    assert not external.valid_signature(SECRET, timestamp, handler.sign("otro", timestamp, body), body, time.time())
    assert not external.valid_signature(SECRET, timestamp, handler.sign(SECRET, timestamp, body), body, time.time() + 600)


# Contratos (API + Lambda + Proveedores)
@pytest.fixture
def catalog(central_sf):
    with central_sf() as db:
        db.add(UniversalProductEntity(sku="EPP-CASCO-001", name="Casco", unit="UNIDAD", supplier="Otro", supplier_cost=Decimal("18000"),
                                      catalog_version=1, is_active=True, aliases=[]))
        db.commit()


@pytest.fixture
def lambda_secret(monkeypatch):
    monkeypatch.setattr(settings, "LAMBDA_SHARED_SECRET", SECRET)


def upload(client, pdf, **form):
    return client.post("/contracts", data={"title": "Suministro EPP 2027", **form},
                       files={"file": ("contrato.pdf", pdf, "application/pdf")})


def run_lambda(client, storage, contract_id, etag="e1"):
    key = f"contracts/{contract_id}/document.pdf"
    result = handler.process_record(s3_record(key, etag), Objects(storage), lambda e: None)
    event = {k: result[k] for k in ("event_id", "kind", "object_key", "outcome", "detail")}
    return signed_post(client, "/integrations/storage-events", event)


def test_flujo_completo_del_contrato(central_client, central_sf, storage, catalog, lambda_secret, monkeypatch):
    created = upload(central_client, contract_pdf())
    assert created.status_code == 201, created.text
    contract_id = created.json()["contract_id"]
    assert created.json()["status"] == "UPLOADED" and storage.get(f"contracts/{contract_id}/document.pdf").body.startswith(b"%PDF")

    response = run_lambda(central_client, storage, contract_id)
    assert response.json()["result"] == "applied"
    assert run_lambda(central_client, storage, contract_id).json()["result"] == "duplicate"
    detail = central_client.get(f"/contracts/{contract_id}").json()
    assert detail["status"] == "EXTRACTED" and detail["metadata_source"] == "DOCUMENT"
    assert detail["supplier_nit"] == "900123456-7" and detail["agreed_prices"] == [{"sku": "EPP-CASCO-001", "price": "17500.00"}]

    monkeypatch.setattr(external, "get_supplier", lambda nit: external.Supplier(nit, "Seguridad Industrial del Valle", "ACTIVE"))
    approved = central_client.post(f"/contracts/{contract_id}/approve", json={"apply_prices": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED" and approved.json()["catalog_updated"] == ["EPP-CASCO-001"]
    with central_sf() as db:
        product = db.get(UniversalProductEntity, "EPP-CASCO-001")
        assert product.supplier_cost == Decimal("17500.00") and product.catalog_version == 2
    assert central_client.post(f"/contracts/{contract_id}/terminate", json={}).json()["status"] == "TERMINATED"


def test_si_proveedores_no_responde_el_contrato_sigue_pendiente(central_client, storage, catalog, lambda_secret, monkeypatch):
    contract_id = upload(central_client, contract_pdf()).json()["contract_id"]
    run_lambda(central_client, storage, contract_id)

    def down(nit):
        raise external.ExternalUnavailable("Proveedores no responde: ConnectTimeout")

    monkeypatch.setattr(external, "get_supplier", down)
    response = central_client.post(f"/contracts/{contract_id}/approve", json={})
    assert response.status_code == 503 and "intente de nuevo" in response.json()["detail"]
    assert central_client.get(f"/contracts/{contract_id}").json()["status"] == "EXTRACTED"

    monkeypatch.setattr(external, "get_supplier", lambda nit: external.Supplier(nit, "X", "SUSPENDED"))
    assert central_client.post(f"/contracts/{contract_id}/approve", json={}).status_code == 422


def test_el_formulario_manda_y_las_diferencias_quedan_como_observacion(central_client, storage, catalog, lambda_secret):
    contract_id = upload(central_client, contract_pdf(), supplier_nit="800555111-2").json()["contract_id"]
    run_lambda(central_client, storage, contract_id)
    detail = central_client.get(f"/contracts/{contract_id}").json()
    assert detail["supplier_nit"] == "800555111-2" and detail["metadata_source"] == "MIXED"
    assert any("no coincide" in note for note in detail["observations"])


def test_archivo_que_no_es_pdf_se_rechaza_al_subirlo(central_client, storage):
    response = upload(central_client, b"hola")
    assert response.status_code == 422


def test_aviso_sin_firma_o_con_firma_falsa(central_client, lambda_secret):
    event = {"event_id": "x#1", "kind": "CONTRACT_PROCESSED", "object_key": "x", "outcome": "OK", "detail": {}}
    assert central_client.post("/integrations/storage-events", json=event).status_code == 401
    assert signed_post(central_client, "/integrations/storage-events", event, secret="otra").status_code == 401
    old = str(int(time.time()) - 3600)
    assert signed_post(central_client, "/integrations/storage-events", event, timestamp=old).status_code == 401


def test_si_el_aviso_se_pierde_la_central_lee_el_resultado_de_s3(central_client, central_sf, storage, catalog):
    contract_id = upload(central_client, contract_pdf()).json()["contract_id"]
    handler.process_record(s3_record(f"contracts/{contract_id}/document.pdf"), Objects(storage), lambda e: False)
    with central_sf() as db:
        db.get(ContractEntity, contract_id).uploaded_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
    assert recover_missing(central_sf, storage) == 1
    assert central_client.get(f"/contracts/{contract_id}").json()["status"] == "EXTRACTED"
    assert recover_missing(central_sf, storage) == 0


def test_aprobar_envia_el_catalogo_a_las_bodegas(central_client, central_sf, storage, catalog, lambda_secret, monkeypatch,
                                                 apply, wh1):
    apply(wh1.heartbeat(0))
    contract_id = upload(central_client, contract_pdf()).json()["contract_id"]
    run_lambda(central_client, storage, contract_id)
    monkeypatch.setattr(external, "get_supplier", lambda nit: external.Supplier(nit, "Seguridad Industrial del Valle", "ACTIVE"))
    central_client.post(f"/contracts/{contract_id}/approve", json={})
    with central_sf() as db:
        commands = db.query(TransferCommandEntity).filter(TransferCommandEntity.command_type == "CATALOG_UPSERT").all()
        assert [(c.warehouse_id, c.payload["supplier_cost"]) for c in commands] == [("WH-001", "17500.00")]
