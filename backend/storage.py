"""File storage for original CV documents.

One small interface, two backends:

* ``local``  - files on disk (default; used with SQLite for local development)
* ``s3``     - any S3-compatible bucket (AWS S3, Cloudflare R2, Supabase Storage,
               MinIO, DigitalOcean Spaces ...) for when SkillBridge is scaled

Switch backend with the STORAGE_BACKEND env var - no code changes needed.
The rest of the app only calls ``get_storage()`` and never knows which backend is active.

Env vars
--------
STORAGE_BACKEND        local (default) | s3
STORAGE_LOCAL_DIR      local only. Default: <folder of DB_PATH>/cv_files
S3_BUCKET              s3 only (required)
S3_REGION              s3 only. Default: auto
S3_ENDPOINT_URL        s3 only. Leave empty for AWS; set for R2 / MinIO / Supabase
S3_ACCESS_KEY_ID       s3 only. Optional if the host provides an IAM role
S3_SECRET_ACCESS_KEY   s3 only
S3_PREFIX              s3 only. Optional key prefix, e.g. "prod/"
"""
from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class StorageError(RuntimeError):
    pass


class StorageBackend(ABC):
    name = "abstract"

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a file. Must NOT raise if the file is already gone."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def presigned_url(self, key: str, expires: int = 300) -> Optional[str]:
        """Temporary direct-download URL, or None if the backend cannot provide one."""
        return None


def _clean_key(key: str) -> str:
    """Reject anything that could escape the storage root."""
    key = key.replace("\\", "/").lstrip("/")
    parts = key.split("/")
    if not key or any(p in ("", ".", "..") for p in parts):
        raise StorageError("Invalid storage key")
    return key


class LocalStorage(StorageBackend):
    name = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _clean_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError("Invalid storage key")
        return p

    def put(self, key, data, content_type="application/octet-stream"):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so a crash never leaves a half-written CV.
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        except Exception:
            try: os.unlink(tmp)
            except OSError: pass
            raise

    def get(self, key):
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as e:
            raise StorageError("File not found") from e

    def delete(self, key):
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def exists(self, key):
        return self._path(key).is_file()


class S3Storage(StorageBackend):
    name = "s3"

    def __init__(self, bucket: str, region: str = "auto", endpoint_url: str = "",
                 access_key: str = "", secret_key: str = "", prefix: str = ""):
        if not bucket:
            raise StorageError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        try:
            import boto3  # imported lazily so local development does not need boto3
            from botocore.config import Config
        except ImportError as e:
            raise StorageError("boto3 is not installed. Run: pip install -r requirements-cloud.txt") from e
        kwargs = {"region_name": region or "auto", "config": Config(signature_version="s3v4")}
        if endpoint_url: kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.client = boto3.client("s3", **kwargs)

    def _k(self, key: str) -> str:
        return self.prefix + _clean_key(key)

    def put(self, key, data, content_type="application/octet-stream"):
        # CVs are personal data: always encrypt at rest and keep the object private.
        extra = {"ContentType": content_type}
        if os.getenv("S3_SSE", "AES256").lower() != "none":
            extra["ServerSideEncryption"] = "AES256"
        try:
            self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, **extra)
        except Exception as e:
            raise StorageError(f"S3 upload failed: {type(e).__name__}") from e

    def get(self, key):
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()
        except Exception as e:
            raise StorageError(f"S3 download failed: {type(e).__name__}") from e

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._k(key))  # S3 delete is idempotent
        except Exception as e:
            raise StorageError(f"S3 delete failed: {type(e).__name__}") from e

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception:
            return False

    def presigned_url(self, key, expires=300):
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": self._k(key)}, ExpiresIn=expires)


_instance: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    """Return the configured backend (created once per process)."""
    global _instance
    if _instance is None:
        backend = os.getenv("STORAGE_BACKEND", "local").strip().lower()
        if backend == "s3":
            _instance = S3Storage(
                bucket=os.getenv("S3_BUCKET", ""),
                region=os.getenv("S3_REGION", "auto"),
                endpoint_url=os.getenv("S3_ENDPOINT_URL", ""),
                access_key=os.getenv("S3_ACCESS_KEY_ID", ""),
                secret_key=os.getenv("S3_SECRET_ACCESS_KEY", ""),
                prefix=os.getenv("S3_PREFIX", ""),
            )
        elif backend == "local":
            root = os.getenv("STORAGE_LOCAL_DIR") or str(Path(os.getenv("DB_PATH", "/data/skillbridge.db")).parent / "cv_files")
            _instance = LocalStorage(root)
        else:
            raise StorageError(f"Unknown STORAGE_BACKEND '{backend}' (use 'local' or 's3')")
    return _instance


def reset_storage_for_tests() -> None:
    global _instance
    _instance = None