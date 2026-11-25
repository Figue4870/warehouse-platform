"""Contratos de proveedores: los sube y gestiona el Líder; el administrador global los consulta."""

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from platform_common.auth import LIDER, Principal
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from central import contracts, external
from central.database import get_db
from central.models import ContractEntity
from central.security import MANAGEMENT, require
from central.storage import storage_from_settings

router = APIRouter(prefix="/contracts", tags=["Contratos"])


class Review(BaseModel):
    note: Optional[str] = Field(None, max_length=1000)


class Approval(Review):
    apply_prices: bool = Field(True, description="Llevar los precios pactados al costo del catálogo universal")


def _get(db: Session, contract_id: str) -> ContractEntity:
    contract = db.get(ContractEntity, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")
    return contract


def _parse_prices(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        items = json.loads(raw)
        assert isinstance(items, list) and all(isinstance(i, dict) and "sku" in i and "price" in i for i in items)
        return items
    except (ValueError, AssertionError):
        raise HTTPException(status_code=422, detail='agreed_prices debe ser JSON: [{"sku": "...", "price": 1234.5}]') from None


@router.post("", status_code=status.HTTP_201_CREATED, summary="Subir un contrato (PDF); la Lambda extrae su metadata")
async def upload(file: UploadFile = File(...), title: str = Form(..., min_length=1, max_length=200),
                 supplier_nit: Optional[str] = Form(None, max_length=30), supplier_name: Optional[str] = Form(None, max_length=200),
                 signed_on: Optional[date] = Form(None), valid_until: Optional[date] = Form(None),
                 agreed_prices: Optional[str] = Form(None, description='[{"sku": "EPP-CASCO-001", "price": 17500}]'),
                 db: Session = Depends(get_db), principal: Principal = Depends(require(LIDER))):
    data = await file.read()
    contract = contracts.register_upload(
        db, storage_from_settings(), title=title, filename=file.filename or "contrato.pdf", data=data, principal=principal,
        supplier_nit=supplier_nit or None, supplier_name=supplier_name or None, signed_on=signed_on, valid_until=valid_until,
        agreed_prices=_parse_prices(agreed_prices))
    return contracts.view(contract)


@router.get("", summary="Contratos")
def list_contracts(status_filter: Optional[str] = Query(None, alias="status"), supplier_nit: Optional[str] = None,
                   limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                   _: Principal = Depends(require(*MANAGEMENT))):
    query = db.query(ContractEntity)
    if status_filter:
        query = query.filter(ContractEntity.status == status_filter.upper())
    if supplier_nit:
        query = query.filter(ContractEntity.supplier_nit == external.normalize_nit(supplier_nit))
    return [contracts.view(c) for c in query.order_by(ContractEntity.uploaded_at.desc()).limit(limit).all()]


@router.get("/{contract_id}", summary="Detalle de un contrato")
def get_contract(contract_id: str, db: Session = Depends(get_db), _: Principal = Depends(require(*MANAGEMENT))):
    return contracts.view(_get(db, contract_id))


@router.get("/{contract_id}/document", summary="Descargar el PDF")
def download(contract_id: str, db: Session = Depends(get_db), _: Principal = Depends(require(*MANAGEMENT))):
    contract = _get(db, contract_id)
    try:
        stored = storage_from_settings().get(contract.document_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="El documento no está en el almacenamiento") from None
    return Response(stored.body, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{contract.contract_id}.pdf"'})


@router.post("/{contract_id}/approve", summary="Aprobar (verifica el proveedor y actualiza costos del catálogo)")
def approve(contract_id: str, body: Approval, db: Session = Depends(get_db), principal: Principal = Depends(require(LIDER))):
    contract = _get(db, contract_id)
    result = contracts.approve(db, contract, principal, body.apply_prices, body.note)
    db.commit()
    return {**contracts.view(contract), **result}


@router.post("/{contract_id}/reject", summary="Rechazar")
def reject(contract_id: str, body: Review, db: Session = Depends(get_db), principal: Principal = Depends(require(LIDER))):
    contract = _get(db, contract_id)
    contracts.close(contract, principal, contracts.REJECTED, body.note)
    db.commit()
    return contracts.view(contract)


@router.post("/{contract_id}/terminate", summary="Terminar un contrato aprobado")
def terminate(contract_id: str, body: Review, db: Session = Depends(get_db), principal: Principal = Depends(require(LIDER))):
    contract = _get(db, contract_id)
    contracts.close(contract, principal, contracts.TERMINATED, body.note)
    db.commit()
    return contracts.view(contract)
