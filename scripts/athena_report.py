"""Ejecuta un reporte a través de la API central."""
import argparse
import csv
import json
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?")
    parser.add_argument("--central", default="http://localhost:8090")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--sql", action="store_true")
    parser.add_argument("--csv")
    args = parser.parse_args()
    if not (args.list or args.report):
        parser.error("indica un reporte o usa --list")
    with httpx.Client(base_url=args.central, timeout=15) as client:
        base = "/reports/athena"
        if args.list:
            print(json.dumps(client.get(base).raise_for_status().json(), ensure_ascii=False, indent=2))
            return
        params = {"days": args.days}
        route = f"{base}/{args.report}"
        if args.sql:
            print(client.get(f"{route}/sql", params=params).raise_for_status().json()["sql"])
            return
        result = client.post(route, params=params).raise_for_status().json()
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            report = client.get(f"{base}/executions/{result['execution_id']}").raise_for_status().json()
            if report["status"] == "SUCCEEDED":
                if args.csv:
                    with open(args.csv, "w", encoding="utf-8", newline="") as file:
                        writer = csv.writer(file)
                        writer.writerow(report["columns"])
                        writer.writerows(report["rows"])
                else:
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                return
            if report["status"] in {"FAILED", "CANCELLED"}:
                raise SystemExit(report.get("reason") or report["status"])
            time.sleep(2)
        raise SystemExit("Se agotó el tiempo de espera del reporte")


if __name__ == "__main__":
    main()
