"""Correlation ID que acompaña una operación por todos los componentes."""

import contextvars
import uuid
from datetime import datetime, timezone
from typing import Optional

_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    """Formato del README: COR-20260928-1A2B3C4D"""
    return f"COR-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def set_correlation_id(value: Optional[str]) -> contextvars.Token:
    return _correlation_id.set(value)


def reset_correlation_id(token: contextvars.Token) -> None:
    _correlation_id.reset(token)
