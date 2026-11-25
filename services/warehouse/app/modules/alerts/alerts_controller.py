from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from platform_common.auth import Principal
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.alerts.alerts_service import AlertService
from app.modules.alerts.schemas import AlertResponse
from app.security import ANY_USER, WAREHOUSE_ADMINS, require

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get("", response_model=List[AlertResponse], summary="Alertas automáticas (agotados y desfases)",
            dependencies=[Depends(require(*ANY_USER))])
def list_alerts(
    limit: int = Query(50, ge=1, le=500),
    type: Optional[Literal["OUT_OF_STOCK", "DISCREPANCY", "INCONSISTENCY"]] = Query(None),
    product_id: Optional[int] = Query(None, gt=0),
    pending: bool = Query(False, description="Solo las que nadie ha revisado"),
    db: Session = Depends(get_db),
):
    return AlertService(db).list_alerts(limit, type, product_id, pending)


@router.post("/{alert_id}/ack", response_model=AlertResponse, summary="Marcar una alerta como revisada")
def acknowledge(alert_id: int, db: Session = Depends(get_db), principal: Principal = Depends(require(*WAREHOUSE_ADMINS))):
    alert = AlertService(db).acknowledge(alert_id, principal.username)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")
    return alert
