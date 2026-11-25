"""Emulación local de la Lambda: vigila la carpeta que reemplaza al bucket y, ante un archivo nuevo, invoca el
mismo process_record que corre en AWS con un evento S3 ObjectCreated equivalente.
"""

import hashlib
import logging
import os
import signal
import sys
import time
from pathlib import Path

from storage_processor.handler import CONTRACT_KEY, REPORT_KEY, process_record

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


class FolderObjects:
    def __init__(self, base: Path):
        self.base = base

    def get(self, key: str) -> bytes:
        return (self.base / key).read_bytes()

    def put(self, key: str, body: bytes, content_type: str) -> None:
        path = self.base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)


def result_key(key: str) -> str | None:
    if match := CONTRACT_KEY.match(key):
        return f"contracts/{match.group(1)}/metadata.json"
    if match := REPORT_KEY.match(key):
        return f"reports/summaries/{match.group(1)}/{match.group(2)}/{match.group(3)}.json"
    return None


def pending(base: Path, seen: dict) -> list[str]:
    keys = []
    for folder in ("contracts", "reports/daily"):
        for path in sorted((base / folder).rglob("*")) if (base / folder).is_dir() else ():
            key = path.relative_to(base).as_posix()
            target = result_key(key)
            if target is None:
                continue
            stamp = path.stat().st_mtime_ns
            if seen.get(key) == stamp:
                continue
            result = base / target
            if key not in seen and result.is_file() and result.stat().st_mtime_ns >= stamp:
                seen[key] = stamp  # ya procesado antes de reiniciar
                continue
            seen[key] = stamp
            keys.append(key)
    return keys


def main() -> None:
    base = Path(os.environ.get("BUCKET_DIR", "/app/bucket"))
    interval = float(os.environ.get("POLL_SECONDS", "2"))
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    objects, seen = FolderObjects(base), {}
    logging.info('{"service": "storage-lambda-local", "event": "started", "bucket_dir": "%s"}', base)
    while not stop["now"]:
        for key in pending(base, seen):
            body = (base / key).read_bytes()
            record = {"s3": {"bucket": {"name": "local"},
                             "object": {"key": key, "size": len(body), "eTag": hashlib.md5(body).hexdigest()}}}
            try:
                process_record(record, objects)
            except Exception as error:  # noqa: BLE001 - igual que en AWS: se reintenta en la siguiente vuelta
                seen.pop(key, None)
                logging.warning('{"service": "storage-lambda-local", "event": "failed", "key": "%s", "error": "%s"}', key, error)
        time.sleep(interval)


if __name__ == "__main__":
    main()
