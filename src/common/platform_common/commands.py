from platform_common.messaging import Exchanges, QueueSpec, declare_exchanges, declare_queue

COMMAND_EXCHANGES = Exchanges("central.commands", "central.commands.retry", "central.commands.dlq")


def command_queue(warehouse):
    return QueueSpec(f"commands.{warehouse}", [warehouse], 5000, 10)


def declare_commands(channel, warehouses):
    declare_exchanges(channel, COMMAND_EXCHANGES)
    for warehouse in warehouses:
        declare_queue(channel, command_queue(warehouse), COMMAND_EXCHANGES)
