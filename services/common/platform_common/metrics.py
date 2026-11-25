"""Métricas en formato de texto de Prometheus (sin dependencias) y su resumen en JSON.

Cada proceso lleva su propio registro: con varias instancias detrás del balanceador, Prometheus consulta cada
instancia por separado y suma.
"""

import math
import threading
import time
from bisect import bisect_left
from typing import Callable, Iterable, Optional

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
DB_BUCKETS = (0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1.0)


def _labels(labels: dict) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
                     for k, v in sorted(labels.items()))
    return "{" + inner + "}"


class Counter:
    def __init__(self, name: str, help_text: str):
        self.name, self.help, self.values, self.lock = name, help_text, {}, threading.Lock()

    def inc(self, amount: float = 1.0, **labels) -> None:
        key = tuple(sorted(labels.items()))
        with self.lock:
            self.values[key] = self.values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        lines += [f"{self.name}{_labels(dict(k))} {v:g}" for k, v in sorted(self.values.items())]
        return lines


class Histogram:
    def __init__(self, name: str, help_text: str, buckets: Iterable[float] = LATENCY_BUCKETS):
        self.name, self.help, self.buckets = name, help_text, tuple(buckets)
        self.series: dict[tuple, list] = {}
        self.lock = threading.Lock()

    def observe(self, value: float, **labels) -> None:
        key = tuple(sorted(labels.items()))
        with self.lock:
            counts, total = self.series.setdefault(key, [[0] * (len(self.buckets) + 1), 0.0])
            counts[bisect_left(self.buckets, value)] += 1
            self.series[key][1] = total + value

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for key, (counts, total) in sorted(self.series.items()):
            labels, cumulative = dict(key), 0
            for bound, count in zip(self.buckets, counts):
                cumulative += count
                lines.append(f"{self.name}_bucket{_labels({**labels, 'le': f'{bound:g}'})} {cumulative}")
            cumulative += counts[-1]
            lines.append(f"{self.name}_bucket{_labels({**labels, 'le': '+Inf'})} {cumulative}")
            lines.append(f"{self.name}_sum{_labels(labels)} {total:.6f}")
            lines.append(f"{self.name}_count{_labels(labels)} {cumulative}")
        return lines

    def merged(self, **match) -> list[int]:
        counts = [0] * (len(self.buckets) + 1)
        for key, (values, _) in self.series.items():
            if all(dict(key).get(k) == v for k, v in match.items()):
                counts = [a + b for a, b in zip(counts, values)]
        return counts

    def quantile(self, q: float, **match) -> Optional[float]:
        """Cuantil aproximado por interpolación lineal dentro del bucket (como histogram_quantile de Prometheus)."""
        counts = self.merged(**match)
        total = sum(counts)
        if not total:
            return None
        rank, cumulative = q * total, 0
        for index, count in enumerate(counts):
            if cumulative + count >= rank and count:
                low = self.buckets[index - 1] if index > 0 else 0.0
                high = self.buckets[index] if index < len(self.buckets) else self.buckets[-1]
                return low + (high - low) * ((rank - cumulative) / count)
            cumulative += count
        return self.buckets[-1]


class Registry:
    def __init__(self, service: str):
        self.service = service
        self.started = time.time()
        self.requests = Counter("http_requests_total", "Peticiones HTTP por método, ruta y código")
        self.latency = Histogram("http_request_duration_seconds", "Duración de las peticiones HTTP")
        self.db_latency = Histogram("db_query_duration_seconds", "Duración de las consultas a PostgreSQL", DB_BUCKETS)
        self.collectors: list[Callable[[], list[str]]] = []

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.inc(method=method, route=route, status=str(status))
        self.latency.observe(seconds, method=method, route=route)

    def add_collector(self, collector: Callable[[], list[str]]) -> None:
        """Función que se evalúa al consultar /metrics (Redis, RabbitMQ, PostgreSQL...)."""
        self.collectors.append(collector)

    def render(self) -> str:
        lines = ['# TYPE process_uptime_seconds gauge', f'process_uptime_seconds{{service="{self.service}"}} {time.time() - self.started:.0f}']
        for metric in (self.requests, self.latency, self.db_latency):
            lines += metric.render()
        for collector in self.collectors:
            try:
                lines += collector()
            except Exception as error:  # noqa: BLE001 - una fuente caída no debe tumbar /metrics
                lines.append(f'collector_errors_total{{collector="{getattr(collector, "__name__", "?")}",error="{type(error).__name__}"}} 1')
        return "\n".join(lines) + "\n"

    def summary(self) -> dict:
        elapsed = max(time.time() - self.started, 1e-9)
        total = sum(self.requests.values.values())
        errors = sum(v for k, v in self.requests.values.items() if dict(k).get("status", "").startswith("5"))
        q = lambda p: None if (v := self.latency.quantile(p)) is None else round(v * 1000, 2)  # noqa: E731
        return {
            "service": self.service, "uptime_seconds": round(elapsed), "requests": int(total),
            "rps_since_start": round(total / elapsed, 2), "error_rate": round(errors / total, 4) if total else 0.0,
            "latency_ms": {"p50": q(0.5), "p95": q(0.95), "p99": q(0.99)},
            "db_latency_ms": {"p50": None if (v := self.db_latency.quantile(0.5)) is None else round(v * 1000, 3),
                              "p95": None if (v := self.db_latency.quantile(0.95)) is None else round(v * 1000, 3)},
        }


def gauge(name: str, value, help_text: str = "", **labels) -> list[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    head = [f"# HELP {name} {help_text}"] if help_text else []
    return head + [f"# TYPE {name} gauge", f"{name}{_labels(labels)} {value}"]


def route_template(request) -> str:
    """Ruta con parámetros (/inventory/{product_id}) para no crear una serie por cada id."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def instrument_fastapi(app, registry: Registry, skip=("/metrics", "/metrics/prometheus", "/health")) -> None:
    @app.middleware("http")
    async def metrics_middleware(request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = route_template(request)
            if route not in skip:
                registry.observe_request(request.method, route, status, time.perf_counter() - start)


def instrument_sqlalchemy(engine, registry: Registry) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _start(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("query_start", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _end(conn, cursor, statement, parameters, context, executemany):
        starts = conn.info.get("query_start")
        if starts:
            registry.db_latency.observe(time.perf_counter() - starts.pop())


def postgres_collector(engine) -> Callable[[], list[str]]:
    """Conexiones activas y bloqueos de la base (pg_stat_activity y pg_locks)."""
    from sqlalchemy import text

    def postgres():
        if engine.dialect.name != "postgresql":
            return []
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT coalesce(state, 'unknown'), count(*) FROM pg_stat_activity "
                                     "WHERE datname = current_database() GROUP BY 1")).all()
            locks = conn.execute(text("SELECT count(*) FILTER (WHERE granted), count(*) FILTER (WHERE NOT granted) "
                                      "FROM pg_locks l JOIN pg_database d ON d.oid = l.database "
                                      "WHERE d.datname = current_database()")).one()
        lines = ["# TYPE db_connections gauge"] + [f'db_connections{{state="{state}"}} {count}' for state, count in rows]
        return lines + gauge("db_locks", locks[0], "Bloqueos concedidos", granted="true") + \
            [f'db_locks{{granted="false"}} {locks[1]}']

    return postgres


def rabbitmq_collector(amqp_url: str, management_url: Optional[str] = None) -> Callable[[], list[str]]:
    """Profundidad, tasas, reintentos y DLQ de cada cola del vhost (API de administración de RabbitMQ)."""
    from urllib.parse import quote, unquote, urlparse

    import httpx

    url = urlparse(amqp_url)
    management_url = management_url or f"http://{url.hostname}:15672"
    vhost = quote(unquote(url.path[1:]) or "/", safe="")
    credentials = (unquote(url.username or "guest"), unquote(url.password or "guest"))

    def rabbitmq():
        response = httpx.get(f"{management_url.rstrip('/')}/api/queues/{vhost}", auth=credentials, timeout=2.0,
                             params={"columns": "name,messages,messages_ready,messages_unacknowledged,consumers,"
                                                "message_stats.publish_details.rate,message_stats.ack_details.rate,"
                                                "message_stats.redeliver_details.rate"})
        response.raise_for_status()
        lines = ["# TYPE rabbitmq_queue_messages gauge", "# TYPE rabbitmq_queue_consumers gauge",
                 "# TYPE rabbitmq_queue_publish_rate gauge", "# TYPE rabbitmq_queue_ack_rate gauge"]
        for queue in response.json():
            kind = "dlq" if queue["name"].endswith(".dlq") else "retry" if queue["name"].endswith(".retry") else "work"
            labels = _labels({"queue": queue["name"], "kind": kind})
            stats = queue.get("message_stats") or {}
            lines += [f"rabbitmq_queue_messages{labels} {queue.get('messages', 0)}",
                      f"rabbitmq_queue_consumers{labels} {queue.get('consumers', 0)}",
                      f"rabbitmq_queue_publish_rate{labels} {(stats.get('publish_details') or {}).get('rate', 0)}",
                      f"rabbitmq_queue_ack_rate{labels} {(stats.get('ack_details') or {}).get('rate', 0)}"]
        return lines

    return rabbitmq
