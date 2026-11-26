"""Gateway de borde: recibe las lecturas MQTT, quita duplicados, marca las atrasadas y las reenvía a la ingesta.

Guardar y reenviar: cada lectura se guarda primero en un buffer en disco (SQLite) y solo entonces se confirma al
broker. Si la ingesta o la red se caen, el buffer crece y se vacía en orden cuando vuelven.
"""

import json
import logging
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pika
from platform_common.messaging import GracefulStop
from pydantic import ValidationError

from iot_service.config import settings
from iot_service.logs import setup_logging
from iot_service.readings import EDGE_QUEUE, Reading, declare_edge_topology

logger = logging.getLogger("iot.gateway")


class Buffer:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, body TEXT)")

    def add(self, reading: Reading) -> bool:
        cursor = self.db.execute("INSERT OR IGNORE INTO pending (event_id, body) VALUES (?, ?)",
                                 (reading.event_id, reading.model_dump_json()))
        return cursor.rowcount == 1

    def take(self, limit: int) -> list[tuple[int, str]]:
        return self.db.execute("SELECT id, body FROM pending ORDER BY id LIMIT ?", (limit,)).fetchall()

    def remove_upto(self, last_id: int) -> None:
        self.db.execute("DELETE FROM pending WHERE id <= ?", (last_id,))

    def size(self) -> int:
        return self.db.execute("SELECT count(*) FROM pending").fetchone()[0]


class Dedupe:
    """Últimas secuencias vistas por sensor (ventana acotada: la memoria no crece con el tiempo)."""

    def __init__(self, window: int):
        self.window = window
        self.seen: dict[str, set] = defaultdict(set)
        self.order: dict[str, deque] = defaultdict(deque)
        self.highest: dict[str, int] = {}

    def check(self, sensor_id: str, sequence: int) -> tuple[bool, bool]:
        """(es_repetida, llegó_atrasada)."""
        if sequence in self.seen[sensor_id]:
            return True, False
        self.seen[sensor_id].add(sequence)
        self.order[sensor_id].append(sequence)
        if len(self.order[sensor_id]) > self.window:
            self.seen[sensor_id].discard(self.order[sensor_id].popleft())
        late = sequence < self.highest.get(sensor_id, -1)
        self.highest[sensor_id] = max(self.highest.get(sensor_id, -1), sequence)
        return False, late


class Gateway:
    def __init__(self, buffer: Buffer, post, gateway_id: str, warehouses: set[str] | None = None):
        self.buffer, self.post, self.gateway_id, self.warehouses = buffer, post, gateway_id, warehouses
        self.dedupe = Dedupe(settings.GATEWAY_DEDUPE_WINDOW)
        self.stats = defaultdict(int)
        self.next_flush = 0.0
        self.backoff = settings.GATEWAY_FLUSH_SECONDS

    def receive(self, body: bytes) -> str:
        self.stats["received"] += 1
        try:
            reading = Reading.model_validate_json(body)
        except ValidationError:
            self.stats["invalid"] += 1
            return "invalid"
        if self.warehouses and reading.warehouse_id not in self.warehouses:
            self.stats["foreign"] += 1
            return "foreign"
        duplicate, late = self.dedupe.check(reading.sensor_id, reading.sequence_number)
        if duplicate:
            self.stats["duplicates_dropped"] += 1
            return "duplicate"
        reading.late = late
        reading.gateway_received_at = datetime.now(timezone.utc)
        self.stats["late"] += int(late)
        self.stats["buffered"] += int(self.buffer.add(reading))
        return "buffered"

    def flush(self, now: float | None = None) -> int:
        now = now or time.monotonic()
        if now < self.next_flush:
            return 0
        sent = 0
        while rows := self.buffer.take(settings.GATEWAY_BATCH_SIZE):
            try:
                self.post({"gateway_id": self.gateway_id, "readings": [json.loads(body) for _, body in rows]})
            except Exception as error:  # noqa: BLE001 - ingesta o red caídas: se guarda y se reintenta con espera creciente
                self.stats["ingest_failures"] += 1
                self.backoff = min(self.backoff * 2, 30.0)
                self.next_flush = now + self.backoff
                logger.warning("Ingesta no disponible; las lecturas quedan en el buffer", extra={
                    "event": "ingest_unavailable", "context": {"buffer": self.buffer.size(), "retry_in_seconds": self.backoff,
                                                               "error": str(error)[:200]}})
                return sent
            self.buffer.remove_upto(rows[-1][0])
            sent += len(rows)
            self.stats["forwarded"] += len(rows)
        self.backoff = settings.GATEWAY_FLUSH_SECONDS
        self.next_flush = now + settings.GATEWAY_FLUSH_SECONDS
        return sent

    def snapshot(self) -> dict:
        return {"gateway_id": self.gateway_id, **self.stats, "buffer_size": self.buffer.size()}


def http_poster(url: str):
    client = httpx.Client(timeout=settings.INGESTION_TIMEOUT_SECONDS,
                          headers={"X-Gateway-Key": settings.GATEWAY_KEY} if settings.GATEWAY_KEY else {})

    def post(batch: dict) -> None:
        response = client.post(f"{url.rstrip('/')}/ingest", json=batch)
        response.raise_for_status()

    return post


def serve_stats(gateway: Gateway, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(gateway.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever, daemon=True).start()


def main() -> None:
    setup_logging("iot-gateway")
    stopper = GracefulStop()
    warehouses = {w.strip() for w in settings.GATEWAY_WAREHOUSES.split(",") if w.strip()} or None
    gateway = Gateway(Buffer(settings.GATEWAY_BUFFER_PATH), http_poster(settings.INGESTION_URL), settings.INSTANCE_NAME, warehouses)
    serve_stats(gateway, settings.GATEWAY_STATS_PORT)
    last_log = 0.0
    while not stopper.stop:
        try:
            connection = pika.BlockingConnection(pika.URLParameters(settings.AMQP_URL))
            channel = connection.channel()
            declare_edge_topology(channel)
            channel.basic_qos(prefetch_count=settings.GATEWAY_BATCH_SIZE)

            def on_message(ch, method, properties, body):
                gateway.receive(body)
                ch.basic_ack(method.delivery_tag)  # ya quedó en el buffer en disco (o se descartó a propósito)

            channel.basic_consume(EDGE_QUEUE.name, on_message)
            logger.info("Gateway escuchando", extra={"event": "gateway_started", "context": {"queue": EDGE_QUEUE.name}})
            while not stopper.stop:
                connection.process_data_events(time_limit=settings.GATEWAY_FLUSH_SECONDS)
                gateway.flush()
                if time.monotonic() - last_log > 10:
                    last_log = time.monotonic()
                    logger.info("Estado del gateway", extra={"event": "gateway_stats", "context": gateway.snapshot()})
            connection.close()
        except pika.exceptions.AMQPError as error:
            logger.warning("Broker MQTT no disponible; reintentando", extra={"event": "broker_unavailable",
                           "context": {"error": str(error) or type(error).__name__}})
            gateway.flush()  # lo que ya estaba en el buffer puede seguir saliendo
            time.sleep(2)


if __name__ == "__main__":
    main()
