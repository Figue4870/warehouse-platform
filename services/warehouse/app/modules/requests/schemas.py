from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class AdjustmentRequestCreate(BaseModel):
    """Desfase entre el sistema y la estantería: el registrador informa lo que contó."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: Literal["ADJUSTMENT"]
    product_id: int = Field(..., gt=0)
    counted_quantity: int = Field(..., ge=0, description="Unidades contadas físicamente")
    lot: Optional[str] = Field(None, max_length=100)
    reason: str = Field(..., min_length=3, max_length=500)


class NewProductRequestCreate(BaseModel):
    """Petición para que el administrador global cree un producto universal nuevo."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: Literal["NEW_PRODUCT"]
    sku: Optional[str] = Field(None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
    unit: str = Field("UNIDAD", min_length=1, max_length=30)
    supplier: Optional[str] = Field(None, max_length=200)
    reason: str = Field(..., min_length=3, max_length=500)


RequestCreate = Union[AdjustmentRequestCreate, NewProductRequestCreate]


class RequestDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    note: Optional[str] = Field(None, max_length=500)


class RequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    warehouse_id: str
    type: str
    status: str
    product_id: Optional[int]
    details: dict
    created_by: str
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    decision_note: Optional[str]
    alert_id: Optional[int]
    movement_id: Optional[int]
    created_at: datetime
    updated_at: datetime
