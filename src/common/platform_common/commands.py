"""Contrato de los comandos y respuestas de la Saga de transferencias (Fase 6)."""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

# comandos
RESERVE = "RESERVE"                # origen: apartar las unidades (RESERVATION)
RECEIVE = "RECEIVE"                # destino: recibir las unidades (TRANSFER_IN)
COMMIT = "COMMIT"                  # origen: despachar lo reservado (TRANSFER_OUT desde la reserva)
RELEASE = "RELEASE"                # origen, COMPENSACIÓN: liberar la reserva (RELEASE)
CANCEL_RECEIVE = "CANCEL_RECEIVE"  # destino, COMPENSACIÓN: "si no la recibiste, ya no la recibas"

COMMAND_TYPES = (RESERVE, RECEIVE, COMMIT, RELEASE, CANCEL_RECEIVE)

# respuestas (eventos de la bodega)
TRANSFER_SOURCE_RESERVED = "TRANSFER_SOURCE_RESERVED"
TRANSFER_SOURCE_REJECTED = "TRANSFER_SOURCE_REJECTED"
TRANSFER_SOURCE_COMMITTED = "TRANSFER_SOURCE_COMMITTED"
TRANSFER_SOURCE_COMMIT_FAILED = "TRANSFER_SOURCE_COMMIT_FAILED"
TRANSFER_SOURCE_RELEASED = "TRANSFER_SOURCE_RELEASED"
TRANSFER_DESTINATION_RECEIVED = "TRANSFER_DESTINATION_RECEIVED"
TRANSFER_DESTINATION_REJECTED = "TRANSFER_DESTINATION_REJECTED"
TRANSFER_DESTINATION_CANCELLED = "TRANSFER_DESTINATION_CANCELLED"

REPLY_TYPES = (
    TRANSFER_SOURCE_RESERVED, TRANSFER_SOURCE_REJECTED, TRANSFER_SOURCE_COMMITTED, TRANSFER_SOURCE_COMMIT_FAILED,
    TRANSFER_SOURCE_RELEASED, TRANSFER_DESTINATION_RECEIVED, TRANSFER_DESTINATION_REJECTED, TRANSFER_DESTINATION_CANCELLED,
)

# Fallas simuladas para los experimentos de la guía (la central decide si se permiten)
SIMULATE_SOURCE_REJECTS = "SOURCE_REJECTS"
SIMULATE_DESTINATION_REJECTS = "DESTINATION_REJECTS"


class InvalidCommandError(ValueError):
    pass


class CommandEnvelope(BaseModel):
    command_id: str = Field(..., min_length=1)
    command_type: str
    transfer_id: str = Field(..., min_length=1)
    warehouse_id: str = Field(..., min_length=1)
    issued_at: str
    deadline: Optional[str] = None
    correlation_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def deadline_passed(self, now: Optional[datetime] = None) -> bool:
        if not self.deadline:
            return False
        deadline = datetime.fromisoformat(self.deadline.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return (now or datetime.now(timezone.utc)) > deadline


def parse_command(body: bytes) -> CommandEnvelope:
    try:
        command = CommandEnvelope.model_validate_json(body)
    except ValidationError as error:
        raise InvalidCommandError(f"Comando inválido: {error.errors()[:3]}") from error
    if command.command_type not in COMMAND_TYPES:
        raise InvalidCommandError(f"Tipo de comando desconocido: {command.command_type}")
    return command
