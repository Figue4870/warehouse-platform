"""Fase 11: pruebas de carga de 100 a 5000 usuarios simulados contra la API de una bodega.

Cada usuario es una conexión que repite, sin pausa, 9 consultas de saldo (GET /inventory/{id}) por cada entrada
(POST /inventory/movements) sobre productos al azar (--products). Durante cada nivel se muestrean las métricas del
servidor (conexiones y esperas de bloqueo de la base, colas) y al final el caché.

  # una configuración (repetir con 1 o 3 instancias y con Redis encendido o apagado)
  python tests/load/load_matrix.py run --target http://localhost:8002 --label "3 instancias, Redis ON" \
      --users 100,500,1000,5000 --duration 30 --out evidencia/carga.jsonl
  # informe en Markdown con todas las configuraciones medidas
  python tests/load/load_matrix.py report evidencia/carga.jsonl > evidencia/carga.md
"""

import argparse
import asyncio
import json
import os
import random
import re
import resource
import statistics
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone

import httpx

READ_SHARE = 0.9


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))]


def raise_open_files():
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    return hard


def login(central: str, username: str, password: str) -> dict:
    response = httpx.post(f"{central}/auth/login", json={"username": username, "password": password}, timeout=10)
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def pick_products(target: str, headers: dict, count: int) -> list[int]:
    with httpx.Client(base_url=target, headers=headers, timeout=10) as client:
        products = client.get("/products", params={"limit": count}).json()
        if not products:
            sys.exit("La bodega no tiene productos: agregue uno del catálogo universal antes de la prueba")
        return [p["id"] for p in products[:count]]


def wait_drained(target: str, limit: float = 90.0) -> float:
    """Después de una carga alta el servidor sigue atendiendo lo que ya aceptó: se espera a que responda rápido."""
    start = time.perf_counter()
    while time.perf_counter() - start < limit:
        try:
            t0 = time.perf_counter()
            httpx.get(f"{target}/health", timeout=5)
            if time.perf_counter() - t0 < 0.5:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return round(time.perf_counter() - start, 1)


class Sampler(threading.Thread):
    """Durante la carga lee /metrics/prometheus cada 2 s y guarda los máximos (conexiones, bloqueos, colas)."""

    def __init__(self, target: str):
        super().__init__(daemon=True)
        self.target, self.stop, self.peaks = target, threading.Event(), Counter()

    def run(self):
        while not self.stop.wait(2):
            try:
                text = httpx.get(f"{self.target}/metrics/prometheus", timeout=3).text
            except httpx.HTTPError:
                self.peaks["unanswered_scrapes"] += 1
                continue
            for name, pattern in (("db_connections", r"^db_connections\{[^}]*\} (\S+)"),
                                  ("db_lock_waits", r'^db_locks\{granted="false"\} (\S+)'),
                                  ("queue_depth", r'^rabbitmq_queue_messages\{kind="work"[^}]*\} (\S+)')):
                value = int(sum(float(v) for v in re.findall(pattern, text, re.M)))
                self.peaks[name] = max(self.peaks[name], value)


def server_metrics(target: str, headers: dict) -> dict:
    try:
        with httpx.Client(base_url=target, headers=headers, timeout=30) as client:
            data = client.get("/metrics").json()
            text = client.get("/metrics/prometheus").text
    except httpx.HTTPError as error:
        return {"cache": {}, "outbox_pending": None, "queue_depth": None, "dlq_depth": None, "db_connections": None,
                "error": type(error).__name__}
    queued = sum(float(v) for v in re.findall(r'^rabbitmq_queue_messages\{kind="work"[^}]*\} (\S+)', text, re.M))
    dlq = sum(float(v) for v in re.findall(r'^rabbitmq_queue_messages\{kind="dlq"[^}]*\} (\S+)', text, re.M))
    connections = sum(float(v) for v in re.findall(r"^db_connections\{[^}]*\} (\S+)", text, re.M))
    return {"cache": data.get("cache", {}), "outbox_pending": (data.get("outbox") or {}).get("pending"),
            "queue_depth": int(queued), "dlq_depth": int(dlq), "db_connections": int(connections)}


async def run_level(targets, headers, product_ids, users, duration, ramp, timeout):
    latencies, statuses, instances, errors = [], Counter(), Counter(), Counter()
    deadline = time.perf_counter() + ramp + duration
    measure_from = time.perf_counter() + ramp
    limits = httpx.Limits(max_connections=users + 10, max_keepalive_connections=users + 10)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, limits=limits) as client:
        async def user(index):
            await asyncio.sleep(ramp * index / max(users, 1))
            base = targets[index % len(targets)]  # varias URL = balanceo del lado del cliente
            while time.perf_counter() < deadline:
                read = random.random() < READ_SHARE
                product_id = random.choice(product_ids)
                start = time.perf_counter()
                try:
                    if read:
                        response = await client.get(f"{base}/inventory/{product_id}")
                    else:
                        response = await client.post(f"{base}/inventory/movements", json={
                            "product_id": product_id, "type": "RECEIPT", "quantity": 1, "reference": "carga"})
                    status = response.status_code
                    instances[response.headers.get("X-Instance", "?")] += 1
                except httpx.TimeoutException:
                    status = "timeout"
                except httpx.HTTPError as error:
                    status = type(error).__name__
                if start >= measure_from:
                    latencies.append((time.perf_counter() - start) * 1000)
                    statuses[status] += 1
                    if not isinstance(status, int) or status >= 500:
                        errors[str(status)] += 1

        await asyncio.gather(*(user(i) for i in range(users)))
    total = sum(statuses.values())
    failed = sum(errors.values())
    return {
        "requests": total, "rps": round(total / duration, 1),
        "p50_ms": round(percentile(latencies, 50), 1) if latencies else None,
        "p95_ms": round(percentile(latencies, 95), 1) if latencies else None,
        "p99_ms": round(percentile(latencies, 99), 1) if latencies else None,
        "mean_ms": round(statistics.fmean(latencies), 1) if latencies else None,
        "error_rate": round(failed / total, 4) if total else None,
        "errors": dict(errors), "statuses": {str(k): v for k, v in statuses.items()}, "instances": dict(instances),
    }


def run(args):
    raise_open_files()
    targets = [t.rstrip("/") for t in args.target.split(",")]
    headers = login(args.central, args.user, args.password) if args.central else {}
    product_ids = pick_products(targets[0], headers, args.products)
    for users in [int(u) for u in args.users.split(",")]:
        for target in targets:
            wait_drained(target, limit=args.drain_limit)
        try:
            with httpx.Client(headers=headers, timeout=30) as client:
                client.post(f"{targets[0]}/metrics/reset")
        except httpx.HTTPError:
            pass  # los contadores de caché son una ayuda: la medición es la del cliente
        print(f"[{args.label}] {users} usuarios durante {args.duration} s...", file=sys.stderr, flush=True)
        sampler = Sampler(targets[0])
        sampler.start()
        result = asyncio.run(run_level(targets, headers, product_ids, users, args.duration, min(args.ramp, users / 50),
                                       args.timeout))
        sampler.stop.set()
        drain = max(wait_drained(t, limit=args.drain_limit) for t in targets)
        after = server_metrics(targets[0], headers)
        row = {"label": args.label, "users": users, "duration_s": args.duration, "targets": targets, "products": len(product_ids),
               "at": datetime.now(timezone.utc).isoformat(), **result, "server": after, "peaks": dict(sampler.peaks),
               "drain_seconds": drain}
        with open(args.out, "a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  {result['rps']} req/s  p50 {result['p50_ms']} ms  p95 {result['p95_ms']} ms  p99 {result['p99_ms']} ms  "
              f"errores {result['error_rate']}  caché {after['cache'].get('hit_ratio')}", file=sys.stderr, flush=True)
        time.sleep(args.pause)


def report(args):
    rows = [json.loads(line) for line in open(args.results, encoding="utf-8") if line.strip()]
    labels = list(dict.fromkeys(r["label"] for r in rows))
    print("| Configuración | Usuarios | req/s | P50 ms | P95 ms | P99 ms | Errores | Hit ratio caché | Conexiones BD (máx.) | Esperas de bloqueo (máx.) | Recuperación |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for label in labels:
        for r in sorted((r for r in rows if r["label"] == label), key=lambda r: r["users"]):
            hit = r["server"]["cache"].get("hit_ratio")
            print(f"| {label} | {r['users']} | {r['rps']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | "
                  f"{(r['error_rate'] or 0) * 100:.1f} % | {'—' if hit is None else f'{hit:.2f}'} | "
                  f"{r.get('peaks', {}).get('db_connections', '—')} | {r.get('peaks', {}).get('db_lock_waits', '—')} | "
                  f"{r.get('drain_seconds', 0)} s |")
    print()
    for r in rows:
        if r["errors"]:
            print(f"- {r['label']}, {r['users']} usuarios: {', '.join(f'{k}: {v}' for k, v in r['errors'].items())}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--target", required=True, help="URL de la API (o varias separadas por coma)")
    run_p.add_argument("--label", required=True, help='Ej. "1 instancia, Redis OFF"')
    run_p.add_argument("--users", default="100,500,1000,5000")
    run_p.add_argument("--duration", type=int, default=30)
    run_p.add_argument("--ramp", type=float, default=10.0, help="Segundos para conectar a todos los usuarios")
    run_p.add_argument("--pause", type=float, default=5.0, help="Pausa entre niveles (que las colas se vacíen)")
    run_p.add_argument("--timeout", type=float, default=10.0)
    run_p.add_argument("--drain-limit", type=float, default=180.0, help="Máximo de segundos esperando a que el servidor se recupere")
    run_p.add_argument("--central", default=os.getenv("CENTRAL_URL", "http://localhost:8090"))
    run_p.add_argument("--user", default=os.getenv("LOAD_USER", "admin.wh001"))
    run_p.add_argument("--password", default=os.getenv("LOAD_PASSWORD", "Bodegas2026!"))
    run_p.add_argument("--products", type=int, default=20, help="Productos entre los que se reparten lecturas y entradas (1 = fila caliente)")
    run_p.add_argument("--out", default="carga.jsonl")
    report_p = sub.add_parser("report")
    report_p.add_argument("results")
    args = parser.parse_args()
    run(args) if args.command == "run" else report(args)


if __name__ == "__main__":
    main()
