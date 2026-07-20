from __future__ import annotations

import hashlib
import importlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from rank_rent.settings import Settings


class BlobStoreError(RuntimeError):
    pass


class ImmutableBlobError(BlobStoreError):
    pass


@dataclass(frozen=True)
class BlobInfo:
    key: str
    checksum: str
    size_bytes: int
    content_type: str


class BlobStore(Protocol):
    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> BlobInfo: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def checksum(self, key: str) -> str: ...


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative_key(key: str) -> PurePosixPath:
    normalized = PurePosixPath(key)
    if not key or normalized.is_absolute() or any(part in {"", ".", ".."} for part in normalized.parts):
        raise BlobStoreError(f"Unsafe blob key: {key!r}")
    return normalized


class FilesystemBlobStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        relative = _safe_relative_key(key)
        path = self.root.joinpath(*relative.parts)
        if not path.is_relative_to(self.root):
            raise BlobStoreError(f"Blob key escapes configured root: {key!r}")
        return path

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> BlobInfo:
        path = self._path(key)
        expected_checksum = _sha256(data)
        if path.exists():
            actual_checksum = self.checksum(key)
            if actual_checksum != expected_checksum:
                raise ImmutableBlobError(f"Blob {key!r} already exists with different content.")
            return BlobInfo(key, actual_checksum, path.stat().st_size, content_type)

        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                actual_checksum = self.checksum(key)
                if actual_checksum != expected_checksum:
                    raise ImmutableBlobError(
                        f"Blob {key!r} was concurrently created with different content."
                    ) from None
        finally:
            temporary_path.unlink(missing_ok=True)
        return BlobInfo(key, expected_checksum, len(data), content_type)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def checksum(self, key: str) -> str:
        digest = hashlib.sha256()
        with self._path(key).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class S3BlobStore:
    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        server_side_encryption: str | None = "AES256",
        client: Any | None = None,
    ) -> None:
        if not bucket.strip():
            raise BlobStoreError("An S3 bucket is required.")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.server_side_encryption = server_side_encryption or None
        if client is None:
            try:
                boto3 = importlib.import_module("boto3")
            except ModuleNotFoundError as exc:
                raise BlobStoreError(
                    "S3 blob storage requires the optional 's3' dependency."
                ) from exc
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url or None,
                region_name=region_name or None,
            )
        self.client = client

    def _key(self, key: str) -> str:
        relative = _safe_relative_key(key).as_posix()
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def _head(self, key: str) -> dict[str, Any] | None:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            response = getattr(exc, "response", {})
            error = response.get("Error", {}) if isinstance(response, dict) else {}
            if str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return dict(response)

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> BlobInfo:
        expected_checksum = _sha256(data)
        head = self._head(key)
        if head is not None:
            metadata = head.get("Metadata") or {}
            actual_checksum = str(metadata.get("sha256") or self.checksum(key))
            if actual_checksum != expected_checksum:
                raise ImmutableBlobError(f"Blob {key!r} already exists with different content.")
            return BlobInfo(
                key,
                actual_checksum,
                int(head.get("ContentLength") or len(data)),
                str(head.get("ContentType") or content_type),
            )

        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._key(key),
            "Body": data,
            "ContentType": content_type,
            "Metadata": {"sha256": expected_checksum},
        }
        if self.server_side_encryption:
            request["ServerSideEncryption"] = self.server_side_encryption
        request["IfNoneMatch"] = "*"
        try:
            self.client.put_object(**request)
        except Exception as exc:
            response = getattr(exc, "response", {})
            error = response.get("Error", {}) if isinstance(response, dict) else {}
            error_code = str(error.get("Code", ""))
            if error_code not in {
                "409",
                "412",
                "ConditionalRequestConflict",
                "PreconditionFailed",
            }:
                raise
            head = self._head(key)
            if head is None:
                raise
            metadata = head.get("Metadata") or {}
            actual_checksum = str(metadata.get("sha256") or self.checksum(key))
            if actual_checksum != expected_checksum:
                raise ImmutableBlobError(
                    f"Blob {key!r} was concurrently created with different content."
                ) from exc
            return BlobInfo(
                key,
                actual_checksum,
                int(head.get("ContentLength") or len(data)),
                str(head.get("ContentType") or content_type),
            )
        return BlobInfo(key, expected_checksum, len(data), content_type)

    def get(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        body = response["Body"]
        return bytes(body.read())

    def exists(self, key: str) -> bool:
        return self._head(key) is not None

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def checksum(self, key: str) -> str:
        return _sha256(self.get(key))


def build_blob_store(settings: Settings) -> BlobStore:
    if settings.blob_store_backend == "filesystem":
        return FilesystemBlobStore(settings.blob_store_path)
    return S3BlobStore(
        settings.blob_store_s3_bucket,
        prefix=settings.blob_store_s3_prefix,
        endpoint_url=settings.blob_store_s3_endpoint_url or None,
        region_name=settings.blob_store_s3_region or None,
        server_side_encryption=settings.blob_store_s3_server_side_encryption or None,
    )
