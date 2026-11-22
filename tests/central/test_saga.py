from sqlalchemy import select

from central.saga import Command


def test_timeout_de_destino_confirma_recepcion_antes_de_compensar(central_client, central_sf, apply, wh1, wh2):
    from central.saga import Transfer, check_timeouts, handle_reply, now
    from datetime import timedelta

    apply(wh1.heartbeat(0), wh2.heartbeat(0))
    response = central_client.post("/transfers", json={
        "sku": "SKU-1", "source_warehouse_id": "WH-001", "destination_warehouse_id": "WH-002",
        "quantity": 30, "timeout_seconds": 5,
    })
    assert response.status_code == 202
    transfer_id = response.json()["transfer_id"]

    def reply(warehouse, step, outcome):
        return warehouse.make("TRANSFER_REPLY", {"transfer_id": transfer_id, "step": step, "outcome": outcome})

    reserve = reply(wh1, "RESERVE", "OK")
    with central_sf.begin() as db:
        handle_reply(db, reserve)
    with central_sf.begin() as db:
        handle_reply(db, reserve)
        transfer = db.scalar(select(Transfer))
        transfer.deadline = now() - timedelta(seconds=60)
        db.flush()
        check_timeouts(db)
    with central_sf.begin() as db:
        assert list(db.scalars(select(Command.step).order_by(Command.id))) == ["RESERVE", "RECEIVE", "CANCEL_RECEIVE"]
        handle_reply(db, reply(wh2, "CANCEL_RECEIVE", "ALREADY_RECEIVED"))
    with central_sf.begin() as db:
        handle_reply(db, reply(wh1, "COMMIT", "OK"))
    result = central_client.get(response.headers["Location"]).json()
    assert result["state"] == "COMPLETED"
    assert [command["step"] for command in result["commands"]] == ["RESERVE", "RECEIVE", "CANCEL_RECEIVE", "COMMIT"]
