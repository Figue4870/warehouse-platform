"""Reportes con Athena: exportación a S3 y consultas (Athena simulado con botocore Stubber)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber

from central.analytics.athena import REPORTS, AthenaReports, render_sql
from central.analytics.exporter import Exporter, export_dataset
from central.storage import LocalStorage, S3Storage
from central.api import app
from central.models import MovementHistoryEntity, TransferEntity
from central.routers.athena import get_athena


def read_jsonl(base: Path, dataset: str) -> dict[str, list[dict]]:
    files = {}
    for path in sorted((base / "exports" / dataset).glob("dt=*/*.json")):
        files[f"{path.parent.name}/{path.name}"] = [json.loads(line) for line in path.read_text().splitlines()]
    return files


@pytest.fixture
def movements(apply, central_sf, wh1, wh2):
    apply(wh1.product(), wh1.movement(quantity=10, after=10), wh1.movement(m_type="SHIPMENT", quantity=3, after=7),
          wh2.movement(quantity=5, after=5))
    with central_sf() as db:  # uno de los movimientos ocurrió AYER (otra partición)
        row = db.query(MovementHistoryEntity).filter(MovementHistoryEntity.warehouse_id == "WH-002").one()
        row.occurred_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()


def test_exporta_jsonl_particionado_por_dia_y_sin_repetir(movements, central_sf, apply, wh1, tmp_path):
    exporter = Exporter(central_sf, LocalStorage(tmp_path), snapshot_seconds=3600)
    result = exporter.run_once()
    assert result["movements"] == 3 and result["inventory"] == 2
    files = read_jsonl(tmp_path, "movements")
    days = {name.split("/")[0] for name in files}
    assert len(days) == 2                                     # hoy y ayer: dos particiones dt=
    assert sum(len(rows) for rows in files.values()) == 3
    shipment = next(r for rows in files.values() for r in rows if r["type"] == "SHIPMENT")
    assert shipment["unit_retail_price"] == "100.00" and shipment["occurred_at"].endswith("Z")

    assert exporter.run_once() == {"movements": 0, "transfers": 0}   # nada nuevo (y la foto aún no toca)
    apply(wh1.movement(m_type="SHIPMENT", quantity=1, after=6))
    assert exporter.run_once()["movements"] == 1


def test_si_s3_falla_no_se_marca_nada_y_se_reintenta(movements, central_sf, tmp_path):
    class FlakyStorage(LocalStorage):
        fail = True

        def put(self, key, body):
            if self.fail:
                raise ConnectionError("S3 no responde (simulado)")
            super().put(key, body)

    storage = FlakyStorage(tmp_path)
    with pytest.raises(ConnectionError):
        export_dataset(central_sf, storage, "movements")
    with central_sf() as db:
        assert db.query(MovementHistoryEntity).filter(MovementHistoryEntity.exported_at.is_(None)).count() == 3
    storage.fail = False
    assert export_dataset(central_sf, storage, "movements") == 3
    assert export_dataset(central_sf, storage, "movements") == 0
    assert sum(len(r) for r in read_jsonl(tmp_path, "movements").values()) == 3


def test_solo_se_exportan_transferencias_terminadas(central_sf, tmp_path):
    now = datetime.now(timezone.utc)
    with central_sf() as db:
        for i, state in enumerate(("COMPLETED", "IN_TRANSIT", "RESERVATION_RELEASED")):
            db.add(TransferEntity(transfer_id=f"TR-{i:06d}", sku="P-001", source_warehouse_id="WH-001",
                                  destination_warehouse_id="WH-002", quantity=5, state=state, timeout_seconds=60,
                                  created_at=now - timedelta(seconds=30), updated_at=now,
                                  finished_at=now if state != "IN_TRANSIT" else None))
        db.commit()
    assert export_dataset(central_sf, LocalStorage(tmp_path), "transfers") == 2
    rows = [r for rows in read_jsonl(tmp_path, "transfers").values() for r in rows]
    assert {r["final_state"] for r in rows} == {"COMPLETED", "RESERVATION_RELEASED"}
    assert all(29 <= r["duration_seconds"] <= 31 for r in rows)


def test_s3_crea_el_bucket_si_no_existe_y_sube(monkeypatch):
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    with Stubber(client) as stub:
        stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404, expected_params={"Bucket": "datos"})
        stub.add_response("create_bucket", {"Location": "/datos"}, {"Bucket": "datos"})
        for name in ("l.json", "m.json"):
            stub.add_response("put_object", {"ETag": '"abc"'}, {"Bucket": "datos", "Key": f"exports/movements/dt=2026-11-16/{name}",
                                                                "Body": ANY, "ContentType": "application/x-ndjson",
                                                                "Metadata": {}})
        storage = S3Storage("datos", client, region="us-east-1")
        storage.put("exports/movements/dt=2026-11-16/l.json", b'{"a": 1}\n')
        storage.put("exports/movements/dt=2026-11-16/m.json", b'{"a": 2}\n')   # ya no vuelve a revisar el bucket
        stub.assert_no_pending_responses()


def test_sql_de_cada_reporte(central_client):
    for name in REPORTS:
        sql = render_sql(name, "warehouse_analytics", 7)
        assert '"warehouse_analytics".' in sql and "-7" in sql and "${" not in sql
    response = central_client.get("/reports/athena/ventas_por_dia/sql", params={"days": 15})
    assert response.status_code == 200 and "date_add('day', -15, current_date)" in response.text
    assert central_client.get("/reports/athena/no_existe/sql").status_code == 404



def test_reporte_de_athena_de_punta_a_punta(central_client):
    client = boto3.client("athena", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.add_response("start_query_execution", {"QueryExecutionId": "q-123"},
                      {"QueryString": render_sql("top_productos", "analytics", 30),
                       "QueryExecutionContext": {"Database": "analytics"}, "WorkGroup": "reportes"})
    stub.add_response("get_query_execution", {"QueryExecution": {
        "QueryExecutionId": "q-123", "Status": {"State": "SUCCEEDED"},
        "Statistics": {"DataScannedInBytes": 2048, "EngineExecutionTimeInMillis": 900},
        "ResultConfiguration": {"OutputLocation": "s3://datos/athena-results/q-123.csv"}}}, {"QueryExecutionId": "q-123"})
    columns = [{"Name": n, "Type": "varchar"} for n in ("sku", "producto", "unidades_vendidas")]
    stub.add_response("get_query_results", {"ResultSet": {
        "ResultSetMetadata": {"ColumnInfo": columns},
        "Rows": [{"Data": [{"VarCharValue": "sku"}, {"VarCharValue": "producto"}, {"VarCharValue": "unidades_vendidas"}]},
                 {"Data": [{"VarCharValue": "P-001"}, {"VarCharValue": "Casco"}, {"VarCharValue": "42"}]}]}},
        {"QueryExecutionId": "q-123", "MaxResults": ANY})
    stub.activate()
    app.dependency_overrides[get_athena] = lambda: AthenaReports(client=client, workgroup="reportes", database="analytics")
    try:
        started = central_client.post("/reports/athena/top_productos", params={"days": 30})
        assert started.status_code == 202 and started.json()["query_execution_id"] == "q-123"
        result = central_client.get(started.json()["status_url"]).json()
        assert result["state"] == "SUCCEEDED" and result["data_scanned_bytes"] == 2048
        assert result["columns"] == ["sku", "producto", "unidades_vendidas"]
        assert result["rows"] == [["P-001", "Casco", "42"]]          # sin la fila de encabezados
        stub.assert_no_pending_responses()
    finally:
        app.dependency_overrides.pop(get_athena)
