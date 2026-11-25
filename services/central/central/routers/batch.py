"""Proceso Batch: historial de ejecuciones, ejecución manual y descarga de lo que generó."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from platform_common.auth import Principal
from pydantic import BaseModel
from sqlalchemy.orm import Session

from central.batch import JOB_NAME, RUNNING, REQUESTED, has_run, request_run, run_view, zone
from central.config import settings
from central.database import get_db
from central.integrations import report_verification
from central.models import BatchRunEntity
from central.projection import now_utc
from central.security import MANAGEMENT, require
from central.storage import storage_from_settings

router = APIRouter(prefix="/batch", tags=["Batch"])

DOWNLOADABLE = ("reports/daily/", "warehouses/", "audit/")


class RunRequest(BaseModel):
    business_date: date | None = None


@router.get("/runs", summary="Ejecuciones del reporte diario")
def list_runs(limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db),
              _: Principal = Depends(require(*MANAGEMENT))):
    rows = (db.query(BatchRunEntity).filter(BatchRunEntity.job_name == JOB_NAME)
            .order_by(BatchRunEntity.id.desc()).limit(limit).all())
    verified = {}
    for run in rows:
        if run.business_date not in verified:
            verified[run.business_date] = report_verification(db, run.business_date)
    return {"job_name": JOB_NAME, "schedule": f"{settings.BATCH_HOUR:02d}:{settings.BATCH_MINUTE:02d} {settings.BATCH_TIMEZONE}",
            "items": [{**run_view(r), "verification": verified[r.business_date] if r.status in ("SUCCEEDED", "PARTIAL") else None}
                      for r in rows]}


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, summary="Volver a generar el reporte de un día")
def create_run(body: RunRequest, db: Session = Depends(get_db), principal: Principal = Depends(require(*MANAGEMENT))):
    today = now_utc().astimezone(zone()).date()
    business_date = body.business_date or today
    if business_date > today:
        raise HTTPException(status_code=422, detail="No se puede generar el reporte de un día que no ha pasado")
    if has_run(db, business_date, statuses=(REQUESTED, RUNNING)):
        raise HTTPException(status_code=409, detail="Ya hay una ejecución pendiente para ese día")
    run = request_run(db, business_date, "MANUAL", principal.username)
    db.commit()
    return run_view(run)


@router.get("/runs/{run_id}", summary="Detalle de una ejecución")
def get_run(run_id: int, db: Session = Depends(get_db), _: Principal = Depends(require(*MANAGEMENT))):
    run = db.get(BatchRunEntity, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Ejecución no encontrada")
    return run_view(run)


@router.get("/files", summary="Descargar un archivo generado por el Batch")
def download(key: str, _: Principal = Depends(require(*MANAGEMENT))):
    if not key.startswith(DOWNLOADABLE) or ".." in key:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    try:
        stored = storage_from_settings().get(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Archivo no encontrado") from None
    filename = key.rsplit("/", 1)[-1]
    return Response(stored.body, media_type=stored.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
