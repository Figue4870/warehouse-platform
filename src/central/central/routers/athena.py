from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from central.analytics.exporter import export_status
from central.database import get_db

from central.analytics.reports import REPORTS, render_sql
from central.config import settings

router = APIRouter(prefix="/reports/athena", tags=["Athena"])


def configured() -> bool:
    return bool(settings.ATHENA_DATABASE and settings.ATHENA_WORKGROUP)


def client():
    if not configured():
        raise HTTPException(503, "Athena no está configurado")
    import boto3

    return boto3.client("athena")


def report_sql(name: str, days: int) -> str:
    try:
        return render_sql(name, settings.ATHENA_DATABASE or "warehouse_platform", days)
    except KeyError as error:
        raise HTTPException(404, "Reporte desconocido") from error


@router.get("")
def list_reports():
    return {"configured": configured(), "reports": REPORTS}


@router.get("/export-status")
def get_export_status(db: Session = Depends(get_db)):
    return export_status(db)


@router.get("/{name}/sql")
def show_sql(name: str, days: int = Query(30, ge=1, le=365)):
    return {"sql": report_sql(name, days)}


@router.post("/{name}", status_code=202)
def start_report(name: str, days: int = Query(30, ge=1, le=365)):
    query = report_sql(name, days)
    result = client().start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": settings.ATHENA_DATABASE},
        WorkGroup=settings.ATHENA_WORKGROUP,
    )
    return {"execution_id": result["QueryExecutionId"]}


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str):
    athena = client()
    execution = athena.get_query_execution(QueryExecutionId=execution_id)["QueryExecution"]
    if execution.get("WorkGroup") != settings.ATHENA_WORKGROUP:
        raise HTTPException(404, "Ejecución desconocida")
    status = execution["Status"]
    if status["State"] != "SUCCEEDED":
        return {"status": status["State"], "reason": status.get("StateChangeReason")}

    pages = athena.get_paginator("get_query_results").paginate(QueryExecutionId=execution_id)
    columns = []
    rows = []
    for index, page in enumerate(pages):
        result = page["ResultSet"]
        records = result.get("Rows", [])
        if index == 0:
            columns = [column["Name"] for column in result["ResultSetMetadata"]["ColumnInfo"]]
            records = records[1:]
        rows.extend([value.get("VarCharValue") for value in record["Data"]] for record in records)
    return {"status": "SUCCEEDED", "columns": columns, "rows": rows}
