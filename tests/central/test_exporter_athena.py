import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from central.analytics.exporter import Storage, export_movements, export_status
from central.models import MovementHistoryEntity
from central.routers import athena


def test_export_reintenta_y_solo_marca_después_de_escribir(central_sf, tmp_path):
    with central_sf.begin() as db:
        db.add(MovementHistoryEntity(
            event_id="evt-1", warehouse_id="WH-001", sku="SKU-1", type="SHIPMENT", quantity=2,
            unit_retail_price=Decimal("25.00"), occurred_at=datetime(2026, 11, 22, tzinfo=timezone.utc),
        ))

    class BrokenStorage:
        def write(self, *args):
            raise OSError("almacenamiento indisponible")

    with central_sf() as db:
        with pytest.raises(OSError):
            export_movements(db, BrokenStorage())
    with central_sf() as db:
        assert export_status(db)["movements"] == {"exported": 0, "pending": 1}
        assert export_movements(db, Storage(tmp_path)) == 1
        assert export_movements(db, Storage(tmp_path)) == 0
        assert export_status(db)["movements"] == {"exported": 1, "pending": 0}
    files = list((tmp_path / "movements" / "dt=2026-11-22").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["unit_retail_price"] == "25.00"


def test_athena_devuelve_todas_las_paginas_sin_la_cabecera(central_client, monkeypatch):
    class FakeAthena:
        def get_query_execution(self, **kwargs):
            return {"QueryExecution": {"WorkGroup": "reports", "Status": {"State": "SUCCEEDED"}}}

        def get_paginator(self, operation):
            assert operation == "get_query_results"
            return self

        def paginate(self, **kwargs):
            def row(value):
                return {"Data": [{"VarCharValue": value}]}

            yield {"ResultSet": {"ResultSetMetadata": {"ColumnInfo": [{"Name": "sku"}]},
                                 "Rows": [row("sku"), row("A")]}}
            yield {"ResultSet": {"Rows": [row("B")]}}

    monkeypatch.setattr(athena.settings, "ATHENA_WORKGROUP", "reports")
    monkeypatch.setattr(athena, "client", FakeAthena)
    response = central_client.get("/reports/athena/executions/query-1")
    assert response.status_code == 200
    assert response.json() == {"status": "SUCCEEDED", "columns": ["sku"], "rows": [["A"], ["B"]]}
