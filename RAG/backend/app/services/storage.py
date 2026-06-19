from io import BytesIO
from pathlib import Path

from minio import Minio

from app.core.config import get_settings


class ObjectStorage:
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.minio_bucket
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put_bytes(self, object_key: str, data: bytes, content_type: str | None = None) -> None:
        self.ensure_bucket()
        self.client.put_object(
            self.bucket,
            object_key,
            BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )

    def download_to_path(self, object_key: str, path: Path) -> None:
        self.ensure_bucket()
        response = self.client.get_object(self.bucket, object_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as handle:
                for chunk in response.stream(1024 * 1024):
                    handle.write(chunk)
        finally:
            response.close()
            response.release_conn()

    def remove_object(self, object_key: str) -> None:
        self.ensure_bucket()
        self.client.remove_object(self.bucket, object_key)
