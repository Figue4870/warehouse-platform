"""Avisos de la Lambda (resultado de procesar un objeto de S3) y su recuperación desde S3 si el aviso no llega."""

import json
import logging
from datetime import timedelta
from typing import Optional

from platform_common.auth import ADMIN_GLOBAL
from sqlalchemy.orm import Session, sessionmaker

from central import contracts
from central.batch import DONE, JOB_NAME, output_keys
from central.config import settings
from central.models import BatchRunEntity, ContractEntity, StorageEventEntity
from central.notifications import WARNING, notify
from central.projection import as_aware, now_utc

logger = logging.getLogger("central.integrations")

CONTRACT_PROCESSED, DAILY_REPORT_VERIFIED = "CONTRACT_PROCESSED", "DAILY_REPORT_VERIFIED"
KINDS = (CONTRACT_PROCESSED, DAILY_REPORT_VERIFIED)
APPLIED, DUPLICATE = "applied", "duplicate"


def summary_key(business_date: str) -> str:
    return f"reports/summaries/{business_date[:4]}/{business_date[5:7]}/{business_date}.json"


def handle_storage_event(db: Session, event: dict) -> str:
    """Idempotente por event_id (llave del objeto + ETag). No hace commit."""
    if db.get(StorageEventEntity, event["event_id"]) is not None:
        return DUPLICATE
    db.add(StorageEventEntity(event_id=event["event_id"], kind=event["kind"], object_key=event["object_key"],
                              outcome=event["outcome"], detail=event.get("detail"), received_at=now_utc()))
    detail = event.get("detail") or {}
    if event["kind"] == CONTRACT_PROCESSED:
        contracts.apply_result(db, detail)
    elif event["kind"] == DAILY_REPORT_VERIFIED and event["outcome"] != "OK":
        notify(db, ADMIN_GLOBAL, "DAILY_REPORT_INVALID", f"El reporte del {detail.get('business_date')} no cuadra",
               "; ".join(detail.get("errors") or []), WARNING, dedupe_key=f"report-invalid:{event['event_id']}")
    logger.info("Aviso de la Lambda aplicado", extra={"event": "storage_event", "context": {
        "event_id": event["event_id"], "kind": event["kind"], "outcome": event["outcome"]}})
    return APPLIED


def _read_json(storage, key: str) -> Optional[dict]:
    try:
        return json.loads(storage.get(key).body)
    except FileNotFoundError:
        return None


def recover_missing(session_factory: sessionmaker, storage, now=None) -> int:
    """Resultados que la Lambda dejó en S3 pero cuyo aviso no llegó (central caída, IP pública cambiada...)."""
    now = now or now_utc()
    limit = now - timedelta(seconds=settings.LAMBDA_RESULT_WAIT_SECONDS)
    recovered = 0
    with session_factory() as db:
        waiting = (db.query(ContractEntity).filter(ContractEntity.status == contracts.UPLOADED,
                                                   ContractEntity.uploaded_at < limit).limit(50).all())
        for contract in waiting:
            metadata = _read_json(storage, contracts.metadata_key(contract.contract_id))
            if metadata is None:
                continue
            outcome = "OK" if metadata.get("status") == contracts.EXTRACTED else "REJECTED"
            recovered += handle_storage_event(db, {
                "event_id": f"{contracts.document_key(contract.contract_id)}#{metadata.get('sha256', '')[:32]}",
                "kind": CONTRACT_PROCESSED, "object_key": contracts.document_key(contract.contract_id),
                "outcome": outcome, "detail": metadata}) == APPLIED
        runs = (db.query(BatchRunEntity).filter(BatchRunEntity.job_name == JOB_NAME, BatchRunEntity.status.in_(DONE),
                                                BatchRunEntity.finished_at < limit,
                                                BatchRunEntity.finished_at > now - timedelta(days=2)).all())
        for run in runs:
            report_key = output_keys(run.business_date, [])["report_json"]
            if db.query(StorageEventEntity.event_id).filter(StorageEventEntity.object_key == report_key,
                                                            StorageEventEntity.received_at >= run.finished_at).first():
                continue
            summary = _read_json(storage, summary_key(run.business_date.isoformat()))
            if summary is None or summary.get("verified_at", "")[:19] < as_aware(run.finished_at).isoformat()[:19]:
                continue
            recovered += handle_storage_event(db, {
                "event_id": f"{report_key}#run-{run.id}", "kind": DAILY_REPORT_VERIFIED, "object_key": report_key,
                "outcome": "OK" if summary.get("status") == "VERIFIED" else "REJECTED", "detail": summary}) == APPLIED
        db.commit()
    if recovered:
        logger.info("Resultados de la Lambda recuperados desde S3", extra={"event": "storage_results_recovered",
                                                                          "context": {"count": recovered}})
    return recovered


def report_verification(db: Session, business_date) -> Optional[dict]:
    key = output_keys(business_date, [])["report_json"]
    event = (db.query(StorageEventEntity).filter(StorageEventEntity.object_key == key)
             .order_by(StorageEventEntity.received_at.desc()).first())
    if event is None:
        return None
    return {"outcome": event.outcome, "received_at": event.received_at, "errors": (event.detail or {}).get("errors", []),
            "summary_key": (event.detail or {}).get("summary_key")}
