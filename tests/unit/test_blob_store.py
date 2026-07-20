from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import ImmutableRawResponseError, RawApiResponseORM, ScanRunORM
from rank_rent.services.cache import RawResponseCache, checksum_payload
from rank_rent.storage.blobs import FilesystemBlobStore, ImmutableBlobError, S3BlobStore


class MissingObjectError(Exception):
    response = {"Error": {"Code": "404"}}


class PreconditionFailedError(Exception):
    response = {"Error": {"Code": "PreconditionFailed"}}


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        try:
            row = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise MissingObjectError from exc
        return {
            "ContentLength": len(row["Body"]),
            "ContentType": row["ContentType"],
            "Metadata": row["Metadata"],
        }

    def put_object(self, **request: Any) -> None:
        object_id = (str(request["Bucket"]), str(request["Key"]))
        if request.get("IfNoneMatch") == "*" and object_id in self.objects:
            raise PreconditionFailedError
        self.objects[object_id] = request

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
        return {"Body": BytesIO(self.objects[(Bucket, Key)]["Body"])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


def test_filesystem_blob_store_is_content_immutable(tmp_path) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    info = store.put("raw/provider/response.json", b'{"ok":true}', content_type="application/json")

    assert store.exists(info.key)
    assert store.get(info.key) == b'{"ok":true}'
    assert store.checksum(info.key) == info.checksum
    assert store.put(info.key, b'{"ok":true}').checksum == info.checksum
    with pytest.raises(ImmutableBlobError):
        store.put(info.key, b'{"ok":false}')
    with pytest.raises(RuntimeError, match="Unsafe blob key"):
        store.put("../escape", b"no")

    store.delete(info.key)
    assert not store.exists(info.key)


def test_s3_adapter_uses_injected_client_without_network() -> None:
    client = FakeS3Client()
    store = S3BlobStore(
        "test-bucket",
        prefix="unit",
        endpoint_url="http://never-called.invalid",
        client=client,
    )

    info = store.put("raw/response.json", b"payload", content_type="application/json")

    stored = client.objects[("test-bucket", "unit/raw/response.json")]
    assert stored["ServerSideEncryption"] == "AES256"
    assert stored["IfNoneMatch"] == "*"
    assert store.get(info.key) == b"payload"
    assert store.exists(info.key)
    assert store.put(info.key, b"payload").checksum == info.checksum
    with pytest.raises(ImmutableBlobError):
        store.put(info.key, b"different")


def test_s3_adapter_handles_a_concurrent_immutable_create() -> None:
    class RacingClient(FakeS3Client):
        def __init__(self, concurrent_body: bytes) -> None:
            super().__init__()
            self.concurrent_body = concurrent_body

        def put_object(self, **request: Any) -> None:
            object_id = (str(request["Bucket"]), str(request["Key"]))
            body = self.concurrent_body
            self.objects[object_id] = {
                **request,
                "Body": body,
                "Metadata": {"sha256": hashlib.sha256(body).hexdigest()},
            }
            raise PreconditionFailedError

    matching_store = S3BlobStore("bucket", client=RacingClient(b"payload"))
    assert matching_store.put("raw/race.json", b"payload").size_bytes == 7

    conflicting_store = S3BlobStore("bucket", client=RacingClient(b"other"))
    with pytest.raises(ImmutableBlobError, match="concurrently created"):
        conflicting_store.put("raw/race.json", b"payload")


def test_raw_response_blob_metadata_and_lineage_are_immutable(tmp_path) -> None:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    store = FilesystemBlobStore(tmp_path / "raw-responses")
    response = {"tasks": [{"status_code": 20000, "result": []}]}

    with Session() as session:
        scan = ScanRunORM(source="unit", status="completed")
        session.add(scan)
        session.flush()
        cache = RawResponseCache(
            session,
            "dataforseo-sandbox",
            "v3",
            blob_store=store,
            storage_backend="filesystem",
            encryption_status="not_encrypted",
        )
        cache.set(
            "/endpoint",
            {"tasks": [{"keyword": "drywall"}]},
            response,
            source_scan_run_id=scan.id,
        )
        session.commit()

        row = session.query(RawApiResponseORM).one()
        assert row.response_json == {}
        assert row.object_key is not None
        assert row.storage_backend == "filesystem"
        assert row.content_type == "application/json"
        assert row.size_bytes == len(store.get(row.object_key))
        assert row.checksum == checksum_payload(response)
        assert row.source_scan_run_id == scan.id
        assert row.retention_classification == "raw_provider_response"
        assert row.encryption_status == "not_encrypted"
        assert isinstance(row.blob_created_at, datetime)
        assert row.blob_created_at.tzinfo in {UTC, None}
        assert cache.get("/endpoint", {"tasks": [{"keyword": "drywall"}]}) == response

        row.provider = "changed-provider"
        with pytest.raises(ImmutableRawResponseError, match="provider"):
            session.commit()
        session.rollback()

        object_key = row.object_key

    Base.metadata.drop_all(engine)
    assert object_key is not None
    assert store.exists(object_key)
    assert store.get(object_key)
