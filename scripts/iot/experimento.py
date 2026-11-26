"""Fase 12: experimento de 100 sensores en 5 bodegas y su informe.

Corre el simulador, espera a que el gateway y las colas se vacíen y compara lo enviado con lo guardado:
throughput, latencia, pérdida, duplicados y comportamiento ante caída de red.

  python scripts/iot/experimento.py --broker localhost --iot-api http://localhost:8095 --gateway http://localhost:8096 \
      --duration 180 --duplicates 0.02 --reorder 0.02 --network-drop 40:20 --anomalies --out iot_corrida.md
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def wait_drained(iot_api: str, gateway: str, expected: int, since: str, limit: float = 120) -> dict:
    start, result = time.time(), {}
    while time.time() - start < limit:
        stats = httpx.get(gateway, timeout=5).json() if gateway else {"buffer_size": 0}
        result = httpx.get(f"{iot_api}/iot/experiment", params={"since": since}, timeout=10).json()
        if stats.get("buffer_size", 0) == 0 and result["stored"] >= expected:
            break
        time.sleep(2)
    return result


def opened_summary(alerts: list, since: str) -> str:
    start = datetime.fromisoformat(since)
    opened = [a for a in alerts if datetime.fromisoformat(a["opened_at"]) >= start]
    by_rule: dict = {}
    for alert in opened:
        key = f"{alert['rule']} ({alert['warehouse_id']})"
        by_rule[key] = by_rule.get(key, 0) + 1
    return f"{len(opened)}: " + ", ".join(f"{k} {v}" for k, v in sorted(by_rule.items())) if opened else "0"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="iot")
    parser.add_argument("--password", default="iot")
    parser.add_argument("--iot-api", default="http://localhost:8095")
    parser.add_argument("--gateway", default="http://localhost:8096", help="Estado del gateway (vacío para omitir)")
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--duplicates", type=float, default=0.02)
    parser.add_argument("--reorder", type=float, default=0.02)
    parser.add_argument("--network-drop", default="40:20")
    parser.add_argument("--anomalies", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    gateway_before = httpx.get(args.gateway, timeout=5).json() if args.gateway else {}
    since = datetime.now(timezone.utc).isoformat()
    report_path = Path("simulacion.json").resolve()
    command = [sys.executable, "-m", "iot_service.simulator", "--broker", args.broker, "--port", str(args.port),
               "--username", args.username, "--password", args.password, "--duration", str(args.duration),
               "--interval", str(args.interval), "--duplicates", str(args.duplicates), "--reorder", str(args.reorder),
               "--network-drop", args.network_drop, "--report", str(report_path)] + (["--anomalies"] if args.anomalies else [])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "services" / "common"), str(ROOT / "services" / "iot")])}
    peak_buffer, sim = 0, subprocess.Popen(command, cwd=ROOT / "services" / "iot", stdout=subprocess.DEVNULL, env=env)
    while sim.poll() is None:
        if args.gateway:
            try:
                peak_buffer = max(peak_buffer, httpx.get(args.gateway, timeout=2).json().get("buffer_size", 0))
            except httpx.HTTPError:
                pass
        time.sleep(1)
    sent = json.loads(report_path.read_text())
    result = wait_drained(args.iot_api, args.gateway, sent.get("unique", 0), since)
    gateway_after = httpx.get(args.gateway, timeout=5).json() if args.gateway else {}
    delta = {k: gateway_after.get(k, 0) - gateway_before.get(k, 0) for k in ("received", "duplicates_dropped", "late", "forwarded")}
    alerts = httpx.get(f"{args.iot_api}/iot/alerts", params={"open": "false", "limit": 1000}, timeout=10).json()

    unique, stored = sent.get("unique", 0), result["stored"]
    lines = [
        f"# Experimento IoT: {sent['sensors']} sensores en {len(sent['warehouses'])} bodegas",
        "",
        f"Duración {sent['duration_s']:.0f} s, una lectura cada {sent['interval_s']} s por sensor, duplicados inyectados "
        f"{args.duplicates:.0%}, fuera de orden {args.reorder:.0%}, red caída {args.network_drop.replace(':', ' s durante ')} s.",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Lecturas únicas generadas | {unique} |",
        f"| Publicaciones MQTT (incluye duplicados) | {sent.get('published', 0) + sent.get('duplicates_sent', 0)} |",
        f"| Duplicados enviados por los sensores | {sent.get('duplicates_sent', 0)} |",
        f"| Duplicados descartados en el gateway | {delta['duplicates_dropped']} |",
        f"| Lecturas fuera de orden (reordenadas por el sensor) | {sent.get('reordered', 0)} |",
        f"| Marcadas como atrasadas al guardarse | {result['late']} |",
        f"| Guardadas en la base | {stored} |",
        f"| Pérdida (únicas generadas − guardadas) | {unique - stored} ({(unique - stored) / unique:.2%}) |" if unique else "| Pérdida | — |",
        f"| Guardadas en el sensor durante la caída de red | {sent.get('buffered_on_sensor', 0)} |",
        f"| Máximo en el buffer del gateway | {peak_buffer} |",
        f"| Throughput (lecturas guardadas por segundo) | {result['throughput_per_second']} |",
        f"| Latencia sensor → base P50 / P95 / P99 (ms) | {result['latency_ms']['p50']} / {result['latency_ms']['p95']} / {result['latency_ms']['p99']} |",
        f"| Latencia máxima (ms, incluye lo retenido por la caída) | {result['latency_ms']['max']} |",
        f"| Latencia sensor → gateway P50 / P95 (ms) | {result['sensor_to_gateway_ms']['p50']} / {result['sensor_to_gateway_ms']['p95']} |",
        f"| Alertas abiertas durante el experimento | {opened_summary(alerts, since)} |",
        "",
        "Por bodega: " + ", ".join(f"{w} {n}" for w, n in sorted(result["per_warehouse"].items())),
    ]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    report_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
