import json
import re
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import text

from app.cache import get_inventory_cache
from app.config import settings
from app.database import SessionLocal
from app.modules.inventory.transfer_operations import Operation
from app.events import types
from app.events.outbox import OutboxWriter
from app.events.payloads import product_payload
from app.modules.inventory.inventory_service import InventoryService
from app.modules.inventory.schemas import MovementCreate
from app.modules.products.products_repository import ProductRepository
from app.modules.products.schemas import ProductCreate
from app.modules.shared.errors import BusinessRuleError
from platform_common.commands import COMMAND_EXCHANGES, command_queue, declare_commands
from platform_common.messaging import Consumer, PermanentError


def execute(db, command):
    transfer_id, step = command["transfer_id"], command["step"]
    # Un comando tardío y su compensación nunca deciden simultáneamente.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(320006, hashtext(:id))"), {"id": transfer_id})
    prior = db.get(Operation, (transfer_id, step))
    if prior:
        return prior.result, None

    def succeeded(other):
        operation = db.get(Operation, (transfer_id, other))
        return operation is not None and operation.result["outcome"] == "OK"

    def apply():
        if step == "CANCEL_RECEIVE":
            return ("ALREADY_RECEIVED" if succeeded("RECEIVE") else "OK"), None
        if step == "RELEASE" and (not succeeded("RESERVE") or succeeded("COMMIT")):
            return "NOOP", None
        if step == "COMMIT" and (not succeeded("RESERVE") or succeeded("RELEASE")):
            raise BusinessRuleError("La reserva de la transferencia no está disponible")
        if step in {"RECEIVE", "RESERVE"}:
            if datetime.fromisoformat(command["deadline"]) <= datetime.now(timezone.utc):
                raise BusinessRuleError("Comando vencido")
            if (step == "RESERVE" and db.get(Operation, (transfer_id, "RELEASE"))) or (
                step == "RECEIVE" and db.get(Operation, (transfer_id, "CANCEL_RECEIVE"))
            ):
                raise BusinessRuleError("Transferencia compensada")
            if command.get("simulate_failure") == {"RESERVE": "SOURCE_REJECTS", "RECEIVE": "DESTINATION_REJECTS"}[step]:
                raise BusinessRuleError("Rechazo simulado")

        service = InventoryService(db, get_inventory_cache())
        row = service.repo.get_by_sku(command["sku"])
        if row is None and step == "RECEIVE" and command.get("product"):
            product = ProductRepository(db).create(ProductCreate(sku=command["sku"], **command["product"]))
            service.initialize_stock(product.id)
            db.flush()
            OutboxWriter(db).add(types.PRODUCT_CREATED, "product", product.sku, product_payload(product))
            row = service.repo.get_by_sku(command["sku"])
        if row is None:
            raise BusinessRuleError("SKU sin inventario")
        identifier = row[1].id
        movement_type = {"RESERVE": "RESERVATION", "RECEIVE": "TRANSFER_IN",
                         "RELEASE": "RELEASE", "COMMIT": "TRANSFER_OUT"}[step]
        service.apply_movement(MovementCreate(product_id=identifier, type=movement_type,
                                               quantity=command["quantity"], reference=transfer_id),
                               transfer_step=True)
        return "OK", identifier

    try:
        with db.begin_nested():
            outcome, product_id = apply()
        result = {"outcome": outcome}
    except (BusinessRuleError, ValidationError) as error:
        result, product_id = {"outcome": "FAILED", "reason": str(error)}, None
    db.add(Operation(transfer_id=transfer_id, step=step, result={
        **result, "sku": command["sku"], "quantity": command["quantity"],
    }))
    return result, product_id


def receive(body, properties, routing_key):
    try:
        command = json.loads(body)
        step = command["step"]
        if (command["warehouse_id"] != settings.WAREHOUSE_ID
            or not re.fullmatch(r"TR-[0-9]{6,10}", command["transfer_id"])
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", command["sku"])
            or step not in {"RESERVE", "RECEIVE", "COMMIT", "RELEASE", "CANCEL_RECEIVE"}
            or type(command["quantity"]) is not int or not 0 < command["quantity"] <= 2147483647):
            raise ValueError()
        if step in {"RESERVE", "RECEIVE"} and datetime.fromisoformat(command["deadline"]).tzinfo is None:
            raise ValueError()
    except (ValueError, KeyError, TypeError) as error:
        raise PermanentError("Comando de transferencia inválido") from error
    with SessionLocal.begin() as db:
        result, product_id = execute(db, command)
        OutboxWriter(db).add("TRANSFER_REPLY", "transfer", command["transfer_id"], {
            "transfer_id": command["transfer_id"], "step": step, **result,
        })
    if product_id is not None:
        InventoryService(db, get_inventory_cache()).invalidate_cache(product_id)


if __name__ == "__main__":
    warehouse_id = settings.WAREHOUSE_ID
    if settings.CENTRAL_AMQP_URL:
        Consumer(settings.CENTRAL_AMQP_URL, f"commands-{warehouse_id}", command_queue(warehouse_id),
                 COMMAND_EXCHANGES, receive, lambda channel: declare_commands(channel, [warehouse_id]),
                 prefetch=1).run()
