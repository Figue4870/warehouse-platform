import logging
from typing import List, Optional

from fastapi import HTTPException, status
from platform_common.auth import REGISTRADOR, Principal
from sqlalchemy.orm import Session

from app.cache import InventoryCache
from app.config import settings
from app.events import types as events
from app.events.outbox import OutboxWriter
from app.events.payloads import alert_payload
from app.modules.alerts.alerts_service import DISCREPANCY, AlertService
from app.modules.inventory.inventory_repository import InventoryRepository
from app.modules.inventory.inventory_service import InventoryService
from app.modules.inventory.schemas import MovementType
from app.modules.requests.models import OperationRequestEntity
from app.modules.requests.schemas import AdjustmentRequestCreate, NewProductRequestCreate
from app.modules.shared.errors import BusinessRuleError
from app.modules.shared.utils import schemas_utils

logger = logging.getLogger("warehouse.requests")

PENDING, FORWARDED, APPROVED, REJECTED = "PENDING", "FORWARDED", "APPROVED", "REJECTED"


class RequestService:
    def __init__(self, db: Session, cache: InventoryCache | None = None):
        self.db = db
        self.inventory = InventoryService(db, cache)
        self.inventory_repo = InventoryRepository(db)
        self.alerts = AlertService(db)
        self.outbox = OutboxWriter(db)

    def _get(self, request_id: int) -> OperationRequestEntity:
        row = self.db.get(OperationRequestEntity, request_id)
        if row is None or row.warehouse_id != settings.WAREHOUSE_ID:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada")
        return row

    def create(self, data, principal: Principal) -> OperationRequestEntity:
        if isinstance(data, AdjustmentRequestCreate):
            return self._create_adjustment(data, principal)
        return self._create_new_product(data, principal)

    def _create_adjustment(self, data: AdjustmentRequestCreate, principal: Principal) -> OperationRequestEntity:
        """Registra el desfase como alerta (log) y avisa al administrador de bodega de forma asíncrona."""
        row = self.inventory_repo.get_by_product_for_update(data.product_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El producto no tiene inventario en esta bodega")
        inventory, product = row
        difference = data.counted_quantity - inventory.quantity
        if difference == 0:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El conteo coincide con el sistema: no hay desfase")
        alert = self.alerts.register(
            product.id, DISCREPANCY,
            f"Desfase reportado por {principal.username} en {product.sku}: el sistema tiene {inventory.quantity}, "
            f"se contaron {data.counted_quantity} ({difference:+d}). Motivo: {data.reason}",
            None,
        )
        request = OperationRequestEntity(
            warehouse_id=settings.WAREHOUSE_ID, type="ADJUSTMENT", status=PENDING, product_id=product.id,
            created_by=principal.username, alert_id=alert.id,
            details={"sku": product.sku, "product_name": product.name, "system_quantity": inventory.quantity,
                     "counted_quantity": data.counted_quantity, "difference": difference, "lot": data.lot,
                     "reason": data.reason},
        )
        self.db.add(request)
        self.outbox.add(events.STOCK_DISCREPANCY, "alert", alert.id, alert_payload(alert, product))
        self.db.commit()
        self.db.refresh(request)
        logger.warning("Desfase de inventario reportado", extra={"event": "discrepancy_reported", "context": {
            "request_id": request.id, "sku": product.sku, "difference": difference, "reported_by": principal.username}})
        return request

    def _create_new_product(self, data: NewProductRequestCreate, principal: Principal) -> OperationRequestEntity:
        request = OperationRequestEntity(
            warehouse_id=settings.WAREHOUSE_ID, type="NEW_PRODUCT", status=PENDING, created_by=principal.username,
            details=data.model_dump(exclude={"type"}),
        )
        self.db.add(request)
        self.db.commit()
        self.db.refresh(request)
        logger.info("Solicitud de producto nuevo", extra={"event": "product_request_created", "context": {
            "request_id": request.id, "name": data.name, "requested_by": principal.username}})
        return request

    def list(self, principal: Principal, status_filter: Optional[str], limit: int) -> List[OperationRequestEntity]:
        query = self.db.query(OperationRequestEntity).filter(OperationRequestEntity.warehouse_id == settings.WAREHOUSE_ID)
        if principal.role == REGISTRADOR:
            query = query.filter(OperationRequestEntity.created_by == principal.username)
        if status_filter:
            query = query.filter(OperationRequestEntity.status == status_filter)
        return query.order_by(OperationRequestEntity.id.desc()).limit(limit).all()

    def approve(self, request_id: int, principal: Principal, note: Optional[str]) -> OperationRequestEntity:
        request = self._get(request_id)
        if request.status != PENDING:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"La solicitud ya está {request.status}")
        if request.type == "ADJUSTMENT":
            return self._approve_adjustment(request, principal, note)
        request.status = FORWARDED
        request.decided_by, request.decided_at, request.decision_note = principal.username, schemas_utils.current_utc_time(), note
        self.outbox.add(events.PRODUCT_REQUEST_SUBMITTED, "request", request.id, {
            "request_id": request.id, "request_code": request.code, "requested_by": request.created_by,
            "approved_by": principal.username, "product": request.details,
        })
        self.db.commit()
        self.db.refresh(request)
        return request

    def _approve_adjustment(self, request, principal: Principal, note: Optional[str]) -> OperationRequestEntity:
        """Aplica la diferencia que se contó. El desfase ya quedó registrado como alerta al reportarlo."""
        details = request.details
        try:
            applied = self.inventory.apply_movement(
                request.product_id, MovementType.ADJUSTMENT, int(details["difference"]), details.get("lot"),
                f"{request.code}: {details.get('reason', '')}"[:200], actor=principal.username, alert_on_adjustment=False,
            )
        except BusinessRuleError as error:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.message)
        request.status, request.movement_id = APPROVED, applied.movement.id
        request.decided_by, request.decided_at, request.decision_note = principal.username, schemas_utils.current_utc_time(), note
        self.db.commit()
        self.inventory.invalidate_cache(request.product_id)
        self.db.refresh(request)
        logger.info("Ajuste aprobado", extra={"event": "adjustment_approved", "context": {
            "request_id": request.id, "difference": details["difference"], "approved_by": principal.username}})
        return request

    def reject(self, request_id: int, principal: Principal, note: Optional[str]) -> OperationRequestEntity:
        request = self._get(request_id)
        if request.status != PENDING:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"La solicitud ya está {request.status}")
        request.status = REJECTED
        request.decided_by, request.decided_at, request.decision_note = principal.username, schemas_utils.current_utc_time(), note
        self.db.commit()
        self.db.refresh(request)
        return request

    def resolve_from_central(self, payload: dict) -> str:
        """La central aprobó o rechazó un producto nuevo. Repetir el comando no cambia nada."""
        request = self.db.get(OperationRequestEntity, int(payload["request_id"]))
        if request is None or request.status != FORWARDED:
            return "NOOP"
        request.status = APPROVED if payload.get("status") == APPROVED else REJECTED
        request.decision_note = payload.get("note") or request.decision_note
        if payload.get("sku"):
            request.details = {**request.details, "sku": payload["sku"]}
        return request.status
