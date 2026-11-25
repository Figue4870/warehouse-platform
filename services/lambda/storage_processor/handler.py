"""Lambda del bucket de la plataforma (evento S3 ObjectCreated).

  contracts/<CT-id>/document.pdf      valida el PDF, extrae proveedor, fechas y precios -> metadata.json
  reports/daily/AAAA/MM/<fecha>.json  verifica el consolidado del Batch -> reports/summaries/AAAA/MM/<fecha>.json

Cada resultado se guarda en S3 y se avisa a la central con una petición firmada (HMAC-SHA256). Si el aviso no
llega, la central lo recupera leyendo el resultado de S3, así que el aviso nunca es la única copia.
Procesar dos veces el mismo objeto produce el mismo resultado (la central lo reconoce por llave + ETag).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from storage_processor.contract_parser import parse_contract
from storage_processor.pdf_text import extract_text, is_complete, is_pdf, page_count

logger = logging.getLogger("storage_processor")
logger.setLevel(logging.INFO)

CONTRACT_KEY = re.compile(r"^contracts/(CT-\d{6})/document\.pdf$")
REPORT_KEY = re.compile(r"^reports/daily/(\d{4})/(\d{2})/(\d{4}-\d{2}-\d{2})\.json$")
CONTRACT_PROCESSED, DAILY_REPORT_VERIFIED = "CONTRACT_PROCESSED", "DAILY_REPORT_VERIFIED"
OK, REJECTED = "OK", "REJECTED"


def max_contract_bytes() -> int:
    return int(os.environ.get("CONTRACT_MAX_BYTES", str(10 * 1024 * 1024)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **context) -> None:
    logger.info(json.dumps({"service": "storage-lambda", "event": event, **context}, ensure_ascii=False))


# contratos
def process_contract(contract_id: str, key: str, objects) -> tuple[str, dict]:
    body = objects.get(key)
    errors = []
    if len(body) > max_contract_bytes():
        errors.append(f"El documento pesa {len(body)} bytes; el máximo es {max_contract_bytes()}")
    if not is_pdf(body):
        errors.append("El archivo no es un PDF")
    elif not is_complete(body):
        errors.append("El PDF está incompleto (falta el final del archivo)")
    pages = page_count(body) if is_pdf(body) else 0
    if is_pdf(body) and pages == 0:
        errors.append("El PDF no tiene páginas")
    text = "" if errors else extract_text(body)
    metadata = {
        "contract_id": contract_id,
        "status": "INVALID" if errors else "EXTRACTED",
        "errors": errors,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "pages": pages,
        "text_chars": len(text),
        "extracted": parse_contract(text) if text else {},
        "processed_at": _now(),
    }
    objects.put(f"contracts/{contract_id}/metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json")
    return (REJECTED if errors else OK), metadata


# reporte diario
def _money(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal("NaN")


def process_report(business_date: str, key: str, objects) -> tuple[str, dict]:
    errors = []
    try:
        report = json.loads(objects.get(key))
    except (ValueError, UnicodeDecodeError):
        report, errors = {}, ["El reporte no es JSON válido"]
    warehouses = report.get("warehouses") if isinstance(report.get("warehouses"), list) else []
    totals = report.get("totals") or {}
    if not errors:
        if report.get("business_date") != business_date:
            errors.append(f"La fecha del reporte ({report.get('business_date')}) no coincide con la del archivo")
        for field in ("purchases_value", "sales_value", "movements"):
            if field not in totals:
                errors.append(f"Falta totals.{field}")
        sales = sum((_money((w.get("sales") or {}).get("value")) for w in warehouses), Decimal(0))
        if "sales_value" in totals and sales != _money(totals["sales_value"]):
            errors.append(f"Las ventas por bodega suman {sales} y el total dice {totals['sales_value']}")
        movements = sum(int(w.get("movements") or 0) for w in warehouses)
        if "movements" in totals and movements != int(totals["movements"]):
            errors.append(f"Los movimientos por bodega suman {movements} y el total dice {totals['movements']}")
    ranking = sorted(warehouses, key=lambda w: _money((w.get("sales") or {}).get("value")), reverse=True)
    summary = {
        "business_date": business_date,
        "status": "INVALID" if errors else "VERIFIED",
        "errors": errors,
        "source_key": key,
        "warehouses": len(warehouses),
        "stale_warehouses": report.get("stale_warehouses", []),
        "totals": totals,
        "top_sales": [{"warehouse_id": w.get("warehouse_id"), "sales_value": (w.get("sales") or {}).get("value")}
                      for w in ranking[:3]],
        "out_of_stock_skus": sum(int(w.get("out_of_stock") or 0) for w in warehouses),
        "verified_at": _now(),
    }
    year, month = business_date[:4], business_date[5:7]
    summary_key = f"reports/summaries/{year}/{month}/{business_date}.json"
    objects.put(summary_key, json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
    summary["summary_key"] = summary_key
    return (REJECTED if errors else OK), summary


# aviso a la central
def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


def notify_central(event: dict) -> bool:
    url, secret = os.environ.get("CENTRAL_URL", "").rstrip("/"), os.environ.get("CENTRAL_SHARED_SECRET", "")
    if not url or not secret:
        return False
    body = json.dumps(event, ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    request = urllib.request.Request(f"{url}/integrations/storage-events", data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Signature-Timestamp": timestamp, "X-Signature": sign(secret, timestamp, body)})
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("CENTRAL_TIMEOUT_SECONDS", "5"))) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        _log("central_notify_failed", event_id=event["event_id"], error=str(error))
        return False


def process_record(record: dict, objects, notify=notify_central) -> dict:
    s3_object = record["s3"]["object"]
    key = urllib.parse.unquote_plus(s3_object["key"])
    etag = (s3_object.get("eTag") or s3_object.get("etag") or "").strip('"')
    if match := CONTRACT_KEY.match(key):
        kind = CONTRACT_PROCESSED
        outcome, detail = process_contract(match.group(1), key, objects)
    elif match := REPORT_KEY.match(key):
        kind = DAILY_REPORT_VERIFIED
        outcome, detail = process_report(match.group(3), key, objects)
    else:
        return {"object_key": key, "skipped": True}
    event = {"event_id": f"{key}#{etag}"[:120], "kind": kind, "object_key": key, "outcome": outcome, "detail": detail}
    delivered = notify(event)
    _log("object_processed", object_key=key, kind=kind, outcome=outcome, central_notified=delivered)
    return {**event, "central_notified": delivered}


class S3Objects:
    def __init__(self, bucket: str, client=None):
        import boto3  # incluido en el runtime de Lambda

        self.bucket, self.client = bucket, client or boto3.client("s3")

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        objects = S3Objects(record["s3"]["bucket"]["name"])
        # Un error inesperado (S3 no responde) se deja subir: Lambda reintenta la invocación asíncrona
        results.append(process_record(record, objects))
    return {"processed": len(results), "results": [{k: r.get(k) for k in ("object_key", "outcome", "central_notified")}
                                                    for r in results]}
