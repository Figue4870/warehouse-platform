"""Contratos de proveedores (caso de uso "Carga y aprobación de contrato").

1. El Líder sube el PDF (y, si quiere, los datos del formulario). La central lo guarda en S3.
2. La Lambda valida el documento y extrae proveedor, fechas y precios pactados (metadata.json).
3. La central combina lo extraído con el formulario y deja el contrato listo para revisión.
4. El Líder lo aprueba: se confirma el proveedor con el sistema de Proveedores (consulta síncrona) y los precios
   pactados pasan al catálogo universal, que llega a todas las bodegas.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from platform_common.auth import ADMIN_GLOBAL, LIDER, Principal
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from central import external, platform_commands
from central.config import settings
from central.models import ContractEntity, UniversalProductEntity
from central.notifications import INFO, WARNING, notify
from central.projection import as_aware, now_utc

logger = logging.getLogger("central.contracts")

UPLOADED, EXTRACTED, INVALID, APPROVED, REJECTED, TERMINATED = (
    "UPLOADED", "EXTRACTED", "INVALID", "APPROVED", "REJECTED", "TERMINATED")
REVIEWABLE = (EXTRACTED, INVALID)
FIELDS = ("supplier_nit", "supplier_name", "signed_on", "valid_until")
LABELS = {"supplier_nit": "NIT", "supplier_name": "proveedor", "signed_on": "fecha de firma", "valid_until": "vigencia"}


def document_key(contract_id: str) -> str:
    return f"contracts/{contract_id}/document.pdf"


def metadata_key(contract_id: str) -> str:
    return f"contracts/{contract_id}/metadata.json"


def _next_id(db: Session) -> str:
    last = db.query(func.max(ContractEntity.contract_id)).scalar()
    return f"CT-{(int(last[3:]) if last else 0) + 1:06d}"


def _prices(items) -> list[dict]:
    clean = {}
    for item in items or []:
        sku, price = str(item.get("sku", "")).strip(), Decimal(str(item.get("price")))
        if sku and price > 0:
            clean[sku] = {"sku": sku, "price": f"{price:.2f}"}
    return list(clean.values())


def register_upload(db: Session, storage, *, title: str, filename: str, data: bytes, principal: Principal,
                    supplier_nit: Optional[str] = None, supplier_name: Optional[str] = None,
                    signed_on: Optional[date] = None, valid_until: Optional[date] = None,
                    agreed_prices: Optional[list] = None) -> ContractEntity:
    if not data:
        raise HTTPException(status_code=422, detail="El archivo está vacío")
    if len(data) > settings.CONTRACT_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"El archivo supera {settings.CONTRACT_MAX_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="El contrato debe ser un PDF")
    if signed_on and valid_until and valid_until < signed_on:
        raise HTTPException(status_code=422, detail="La vigencia no puede terminar antes de la firma")
    for attempt in range(3):
        contract_id = _next_id(db)
        contract = ContractEntity(
            contract_id=contract_id, title=title, supplier_nit=external.normalize_nit(supplier_nit) if supplier_nit else None,
            supplier_name=supplier_name, signed_on=signed_on, valid_until=valid_until, agreed_prices=_prices(agreed_prices),
            document_key=document_key(contract_id), document_name=filename[:200], size_bytes=len(data), status=UPLOADED,
            uploaded_by=principal.username, uploaded_at=now_utc())
        db.add(contract)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()  # otro Líder tomó el mismo número al mismo tiempo
            if attempt == 2:
                raise
    try:
        # La llegada del documento a S3 es lo que dispara la Lambda
        storage.put(contract.document_key, data, "application/pdf",
                    {"contract-id": contract.contract_id, "uploaded-by": principal.username})
    except Exception as error:  # noqa: BLE001 - S3 caído: el contrato queda marcado y el Líder puede subirlo de nuevo
        contract.status, contract.validation_errors = INVALID, [f"No se pudo guardar el documento: {type(error).__name__}"]
        db.commit()
        raise HTTPException(status_code=503, detail="No se pudo guardar el documento; intente de nuevo") from error
    logger.info("Contrato cargado", extra={"event": "contract_uploaded", "context": {
        "contract_id": contract.contract_id, "bytes": len(data), "by": principal.username}})
    return contract


def _known_skus(db: Session, skus) -> set[str]:
    return {row[0] for row in db.query(UniversalProductEntity.sku).filter(UniversalProductEntity.sku.in_(list(skus))).all()}


def apply_result(db: Session, metadata: dict) -> Optional[ContractEntity]:
    """Aplica el resultado de la Lambda. No hace commit. Repetirlo con el mismo resultado no cambia nada."""
    contract = db.get(ContractEntity, metadata.get("contract_id"))
    if contract is None or contract.status not in (UPLOADED, *REVIEWABLE):
        return contract
    contract.sha256, contract.pages = metadata.get("sha256"), metadata.get("pages")
    contract.size_bytes = metadata.get("size_bytes") or contract.size_bytes
    contract.processed_at = now_utc()
    extracted = metadata.get("extracted") or {}
    contract.extracted = extracted
    if metadata.get("status") == INVALID:
        contract.status, contract.validation_errors = INVALID, list(metadata.get("errors") or ["Documento inválido"])
        notify(db, LIDER, "CONTRACT_INVALID", f"Contrato {contract.contract_id} rechazado por validación",
               "; ".join(contract.validation_errors), WARNING, dedupe_key=f"contract-invalid:{contract.contract_id}:{contract.sha256}")
        return contract

    notes, from_document, from_form = [], 0, 0
    for field in FIELDS:
        form_value = getattr(contract, field)
        doc_value = extracted.get(field)
        if field in ("signed_on", "valid_until") and doc_value:
            doc_value = date.fromisoformat(doc_value)
        if field == "supplier_nit" and doc_value:
            doc_value = external.normalize_nit(doc_value)
        if form_value in (None, "") and doc_value:
            setattr(contract, field, doc_value)
            from_document += 1
        elif form_value not in (None, ""):
            from_form += 1
            if doc_value and str(doc_value).casefold() != str(form_value).casefold():
                notes.append(f"El {LABELS[field]} del formulario ({form_value}) no coincide con el del documento ({doc_value})")
    doc_prices = _prices(extracted.get("agreed_prices"))
    if not contract.agreed_prices and doc_prices:
        contract.agreed_prices = doc_prices
        from_document += 1
    elif contract.agreed_prices:
        from_form += 1
        if doc_prices and {p["sku"]: p["price"] for p in doc_prices} != {p["sku"]: p["price"] for p in contract.agreed_prices}:
            notes.append("Los precios del formulario no coinciden con los del documento")
    contract.metadata_source = "DOCUMENT" if not from_form else "FORM" if not from_document else "MIXED"
    if not contract.supplier_nit:
        notes.append("Falta el NIT del proveedor")
    if not contract.signed_on:
        notes.append("Falta la fecha de firma")
    if not contract.agreed_prices:
        notes.append("No hay precios pactados")
    unknown = {p["sku"] for p in contract.agreed_prices} - _known_skus(db, {p["sku"] for p in contract.agreed_prices})
    if unknown:
        notes.append(f"SKU fuera del catálogo universal: {', '.join(sorted(unknown))}")
    contract.validation_errors = notes
    contract.status = EXTRACTED
    notify(db, LIDER, "CONTRACT_READY", f"Contrato {contract.contract_id} listo para revisión",
           f"{contract.supplier_name or 'Proveedor sin nombre'}: {len(contract.agreed_prices)} precio(s) pactado(s)"
           + (f". Observaciones: {len(notes)}" if notes else ""), WARNING if notes else INFO,
           dedupe_key=f"contract-ready:{contract.contract_id}:{contract.sha256}")
    return contract


def approve(db: Session, contract: ContractEntity, principal: Principal, apply_prices: bool, note: Optional[str]) -> dict:
    """No hace commit. Devuelve un resumen de lo que cambió en el catálogo."""
    if contract.status != EXTRACTED:
        raise HTTPException(status_code=409, detail=f"Solo se aprueba un contrato listo para revisión (está {contract.status})")
    missing = [label for field, label in (("supplier_nit", "NIT"), ("signed_on", "fecha de firma")) if not getattr(contract, field)]
    if not contract.agreed_prices:
        missing.append("precios pactados")
    if missing:
        raise HTTPException(status_code=422, detail=f"Faltan datos para aprobar: {', '.join(missing)}")
    today = now_utc().date()
    if contract.signed_on > today:
        raise HTTPException(status_code=422, detail="La fecha de firma está en el futuro")
    if contract.valid_until and contract.valid_until < today:
        raise HTTPException(status_code=422, detail="El contrato ya venció")
    try:
        supplier = external.get_supplier(contract.supplier_nit)
    except external.ExternalUnavailable as error:
        raise HTTPException(status_code=503, detail=f"{error}. El contrato sigue pendiente; intente de nuevo") from error
    if supplier is None:
        raise HTTPException(status_code=422, detail=f"El sistema de Proveedores no conoce el NIT {contract.supplier_nit}")
    if not supplier.active:
        raise HTTPException(status_code=422, detail=f"El proveedor {supplier.name} está {supplier.status} en el sistema de Proveedores")
    contract.supplier_verified = True
    contract.supplier_name = supplier.name
    updated = []
    if apply_prices:
        skus = {p["sku"] for p in contract.agreed_prices}
        unknown = skus - _known_skus(db, skus)
        if unknown:
            raise HTTPException(status_code=422, detail=f"SKU fuera del catálogo universal: {', '.join(sorted(unknown))}")
        for item in contract.agreed_prices:
            product = db.get(UniversalProductEntity, item["sku"])
            product.supplier, product.supplier_cost = supplier.name, Decimal(item["price"])
            product.catalog_version += 1
            product.updated_at = now_utc()
            platform_commands.publish_catalog_product(db, product)
            updated.append(product.sku)
    contract.status, contract.reviewed_by, contract.reviewed_at, contract.review_note = APPROVED, principal.username, now_utc(), note
    notify(db, ADMIN_GLOBAL, "CONTRACT_APPROVED", f"Contrato {contract.contract_id} aprobado",
           f"{supplier.name}. Costos actualizados en el catálogo: {', '.join(updated) or 'ninguno'}", INFO,
           dedupe_key=f"contract-approved:{contract.contract_id}")
    logger.info("Contrato aprobado", extra={"event": "contract_approved", "context": {
        "contract_id": contract.contract_id, "supplier_nit": contract.supplier_nit, "catalog_updated": updated}})
    return {"catalog_updated": updated}


def close(contract: ContractEntity, principal: Principal, to_status: str, note: Optional[str]) -> None:
    allowed = {REJECTED: (UPLOADED, *REVIEWABLE), TERMINATED: (APPROVED,)}[to_status]
    if contract.status not in allowed:
        raise HTTPException(status_code=409, detail=f"Un contrato {contract.status} no se puede pasar a {to_status}")
    contract.status, contract.reviewed_by, contract.reviewed_at, contract.review_note = to_status, principal.username, now_utc(), note


def view(contract: ContractEntity) -> dict:
    return {
        "contract_id": contract.contract_id, "title": contract.title, "status": contract.status,
        "supplier_nit": contract.supplier_nit, "supplier_name": contract.supplier_name,
        "supplier_verified": contract.supplier_verified,
        "signed_on": contract.signed_on.isoformat() if contract.signed_on else None,
        "valid_until": contract.valid_until.isoformat() if contract.valid_until else None,
        "agreed_prices": contract.agreed_prices or [], "metadata_source": contract.metadata_source,
        "observations": contract.validation_errors or [], "extracted": contract.extracted,
        "document": {"key": contract.document_key, "name": contract.document_name, "size_bytes": contract.size_bytes,
                     "sha256": contract.sha256, "pages": contract.pages,
                     "url": f"/contracts/{contract.contract_id}/document"},
        "uploaded_by": contract.uploaded_by, "uploaded_at": as_aware(contract.uploaded_at),
        "processed_at": as_aware(contract.processed_at), "reviewed_by": contract.reviewed_by,
        "reviewed_at": as_aware(contract.reviewed_at), "review_note": contract.review_note,
    }
