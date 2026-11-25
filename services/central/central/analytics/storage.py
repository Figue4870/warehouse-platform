"""Dónde se escriben los archivos exportados: S3 (en AWS) o una carpeta local (desarrollo)."""

import os
from pathlib import Path

from central.config import settings


class LocalStorage:
    """Misma estructura de carpetas que en S3: exports/movements/dt=2026-11-16/<archivo>.json"""

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)

    def put(self, key: str, body: bytes) -> None:
        path = self.base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)  # atómico: nunca queda un archivo a medias

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

    def put(self, key: str, body: bytes) -> None:
        if not self.ready:
            self.ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="application/x-ndjson")

    def describe(self) -> str:
        return f"s3://{self.bucket}"


def storage_from_settings():
    if settings.EXPORT_S3_BUCKET:
        return S3Storage(settings.EXPORT_S3_BUCKET)
    return LocalStorage(settings.EXPORT_DIR)
