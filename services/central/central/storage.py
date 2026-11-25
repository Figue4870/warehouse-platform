"""Almacenamiento de objetos: S3 en AWS o una carpeta local con la misma estructura (desarrollo).

Estructura del bucket:
  warehouses/WH-001/2026/09/inventory-2026-09-28.json   reporte diario de cada bodega (Batch)
  reports/daily/2026/09/2026-09-28.json|.csv           reporte consolidado del día (Batch)
  audit/2026/09/28/events.jsonl                        eventos procesados ese día (Batch)
  exports/<tabla>/dt=YYYY-MM-DD/                        datos para Athena (exportador)
  contracts/<CT-id>/                                   contratos de proveedores y su metadata (Lambda)
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from central.config import settings

# Objetos que disparan la Lambda: (prefijo, sufijo)
LAMBDA_TRIGGERS = (("contracts/", "document.pdf"), ("reports/daily/", ".json"))


@dataclass
class StoredObject:
    key: str
    body: bytes
    content_type: str
    metadata: dict


class LocalStorage:
    def __init__(self, base_dir: str):
        self.base = Path(base_dir)

    def _meta_path(self, key: str) -> Path:
        return self.base / ".meta" / f"{key}.json"

    def put(self, key: str, body: bytes, content_type: str = "application/x-ndjson", metadata: Optional[dict] = None) -> None:
        path = self.base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)  # atómico: nunca queda un archivo a medias
        meta = self._meta_path(key)
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"content_type": content_type, "metadata": metadata or {}}), encoding="utf-8")

    def get(self, key: str) -> StoredObject:
        path = self.base / key
        if not path.is_file():
            raise FileNotFoundError(key)
        meta = json.loads(self._meta_path(key).read_text(encoding="utf-8")) if self._meta_path(key).is_file() else {}
        return StoredObject(key, path.read_bytes(), meta.get("content_type", "application/octet-stream"), meta.get("metadata", {}))

    def list(self, prefix: str = "") -> Iterator[str]:
        root = self.base / prefix
        folder = root if root.is_dir() else root.parent
        if not folder.is_dir():
            return iter(())
        keys = (p.relative_to(self.base).as_posix() for p in folder.rglob("*") if p.is_file())
        return (k for k in sorted(keys) if k.startswith(prefix) and not k.startswith(".meta/") and not k.endswith(".tmp"))

    def describe(self) -> str:
        return f"carpeta local {self.base.resolve()}"


class S3Storage:
    def __init__(self, bucket: str, client=None, region: str | None = None):
        self.bucket = bucket
        self.region = region or settings.AWS_DEFAULT_REGION
        if client is None:
            import boto3  # solo hace falta en AWS

            client = boto3.client("s3", region_name=self.region)
        self.client = client
        self.ready = False

    def ensure_bucket(self) -> bool:
        """Crea el bucket si no existe (en el Learner Lab no lo puede crear Terraform). True si lo creó."""
        from botocore.exceptions import ClientError

        created = False
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            if error.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
                raise
            options = {}
            if self.region != "us-east-1":  # en us-east-1 S3 no acepta LocationConstraint
                options["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
            self.client.create_bucket(Bucket=self.bucket, **options)
            created = True
        self.ready = True
        return created

    def ensure_notifications(self, lambda_arn: str, triggers=None) -> bool:
        """Conecta ObjectCreated de cada (prefijo, sufijo) con la Lambda. Devuelve True si cambió la configuración.

        El sufijo evita que la Lambda se dispare con lo que ella misma escribe (metadata.json, resúmenes).
        """
        triggers = triggers or LAMBDA_TRIGGERS
        wanted = [{"Id": f"lambda-{prefix.strip('/').replace('/', '-')}", "LambdaFunctionArn": lambda_arn,
                   "Events": ["s3:ObjectCreated:*"],
                   "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": prefix}, {"Name": "suffix", "Value": suffix}]}}}
                  for prefix, suffix in triggers]
        current = self.client.get_bucket_notification_configuration(Bucket=self.bucket).get("LambdaFunctionConfigurations", [])
        if sorted(c.get("Id") for c in current) == sorted(w["Id"] for w in wanted) and all(
                c.get("LambdaFunctionArn") == lambda_arn for c in current):
            return False
        self.client.put_bucket_notification_configuration(Bucket=self.bucket,
                                                          NotificationConfiguration={"LambdaFunctionConfigurations": wanted})
        return True

    def put(self, key: str, body: bytes, content_type: str = "application/x-ndjson", metadata: Optional[dict] = None) -> None:
        if not self.ready:
            self.ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type, Metadata=metadata or {})

    def get(self, key: str) -> StoredObject:
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise FileNotFoundError(key) from error
            raise
        return StoredObject(key, response["Body"].read(), response.get("ContentType", "application/octet-stream"),
                            response.get("Metadata", {}))

    def list(self, prefix: str = "") -> Iterator[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]

    def describe(self) -> str:
        return f"s3://{self.bucket}"


def storage_from_settings():
    if settings.EXPORT_S3_BUCKET:
        return S3Storage(settings.EXPORT_S3_BUCKET)
    return LocalStorage(settings.EXPORT_DIR)
