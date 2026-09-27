import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest

from cf_tams.jobs import next_alarm, run_jobs
from cf_tams.repository import SQLiteRepository
from cf_tams.storage import copy_etag, load_backends
from tamoss.domain.model import MediaObjectRecord, ObjectInstance, utc_now
from tamoss.errors import BadRequest, ConfigurationError
from tests.conftest import SQLiteStorage, make_use_cases
from tests.tams.support import PRIMARY_BACKEND_ID, create_video_flow

SECONDARY = UUID("22222222-2222-4222-8222-222222222222")
BACKENDS = {
    "MEDIA": {
        "storage_id": str(PRIMARY_BACKEND_ID),
        "bucket_name": "primary-media",
        "label": "cloudflare:r2",
        "default_storage": True,
    },
    "MEDIA_SECONDARY": {
        "storage_id": str(SECONDARY),
        "bucket_name": "secondary-media",
        "label": "cloudflare:r2-secondary",
        "tags": {"tier": ["secondary"]},
    },
}


async def no_webhooks(*args):
    raise AssertionError("No webhook expected")


def registered(use_cases, *, age=timedelta()):
    repo = use_cases.repository
    backend = repo.default_storage_backend()
    record = MediaObjectRecord(
        "copy-object",
        referenced_by_flows={uuid4()},
        created=utc_now() - age,
        instances=[ObjectInstance(backend, None, backend.label, True)],
    )
    repo.save_object(record)
    use_cases.object_storage.write(record.id, b"exact bytes")
    return record


def enqueue(use_cases, object_id="copy-object"):
    use_cases.repository.atomic(
        lambda: use_cases.objects.register_object_instance(
            object_id=object_id, registration={"storage_id": str(SECONDARY)}
        )
    )


def emulate_copy(use_cases):
    async def copy(job):
        storage = use_cases.object_storage
        key = storage.key(job.storage_key)
        storage.bucket_for(job.destination_storage_backend_id).objects[key] = (
            storage.bucket_for(job.source_storage_backend_id).objects[key]
        )

    use_cases.object_storage.copy = copy


@pytest.mark.parametrize("tamoss_app", [BACKENDS], indirect=True)
def test_selected_backend_upload_verification_and_url_filters(client, tamoss_app):
    use_cases = tamoss_app.state.tamoss_use_cases
    flow, _, _ = create_video_flow(client)
    backends = client.get("/service/storage-backends").json()
    assert len(backends) == 2
    response = client.post(
        f"/flows/{flow}/storage",
        json={"object_ids": ["selected"], "storage_id": str(SECONDARY)},
    )
    assert response.status_code == 201
    allocation = response.json()["media_objects"][0]
    assert urlsplit(allocation["put_url"]["url"]).path.startswith("/secondary-media/")
    use_cases.object_storage.write("selected", b"wrong bucket")
    segment = {"object_id": "selected", "timerange": "[0:0_1:0)"}
    assert client.post(f"/flows/{flow}/segments", json=segment).status_code == 400
    use_cases.object_storage.write(
        "selected",
        b"correct bucket",
        backend=use_cases.repository.get_storage_backend(SECONDARY),
    )
    assert client.post(f"/flows/{flow}/segments", json=segment).status_code == 201
    response = client.get(
        "/objects/selected",
        params={"presigned": "true", "accept_storage_ids": str(SECONDARY)},
    )
    assert response.status_code == 200
    assert "/secondary-media/" in response.json()["get_urls"][0]["url"]
    assert (
        client.get(
            "/objects/selected", params={"accept_storage_ids": str(PRIMARY_BACKEND_ID)}
        ).json()["get_urls"]
        == []
    )


def test_copy_is_durable_coalesced_and_cleanup_cannot_delete_restored_copy(tmp_path):
    path = tmp_path / "store.sqlite"
    use_cases = make_use_cases(path, BACKENDS)
    repo, storage = use_cases.repository, use_cases.object_storage
    record = registered(use_cases, age=timedelta(days=2))
    enqueue(use_cases)
    enqueue(use_cases)
    assert len(repo.pending("object_copies", {"pending"})) == 1
    assert len(repo.get_object(record.id).instances) == 1
    assert next_alarm(repo) is not None
    repo.storage.connection.close()
    restarted = make_use_cases(path, BACKENDS)
    restarted.object_storage.buckets = storage.buckets
    repo = restarted.repository
    emulate_copy(restarted)
    asyncio.run(run_jobs(restarted, no_webhooks))
    assert len(repo.get_object(record.id).instances) == 2
    assert (
        storage.bucket_for(SECONDARY).objects[storage.key(record.storage_key)]
        == b"exact bytes"
    )
    with pytest.raises(BadRequest, match="already holds"):
        enqueue(restarted)
    repo.atomic(
        lambda: restarted.objects.delete_object_instance(
            object_id=record.id, storage_id=SECONDARY, label=None
        )
    )
    enqueue(restarted)
    asyncio.run(run_jobs(restarted, no_webhooks))
    assert repo.list_object_cleanups()[0].status == "done"
    assert (
        storage.bucket_for(SECONDARY).objects[storage.key(record.storage_key)]
        == b"exact bytes"
    )
    assert len(repo.get_object(record.id).instances) == 2
    repo.atomic(
        lambda: restarted.objects.delete_object_instance(
            object_id=record.id, storage_id=PRIMARY_BACKEND_ID, label=None
        )
    )
    with pytest.raises(BadRequest, match="All instances"):
        repo.atomic(
            lambda: restarted.objects.delete_object_instance(
                object_id=record.id, storage_id=SECONDARY, label=None
            )
        )
    asyncio.run(run_jobs(restarted, no_webhooks))
    assert storage.key(record.storage_key) not in storage.bucket.objects
    assert storage.key(record.storage_key) in storage.bucket_for(SECONDARY).objects


def test_failed_copy_and_reallocated_object_keep_original_allocation_identity():
    use_cases = make_use_cases(backends=BACKENDS)
    repo, storage = use_cases.repository, use_cases.object_storage
    old = registered(use_cases, age=timedelta(days=2))
    enqueue(use_cases)

    async def failed(job):
        # Physical copy succeeds, then the response is lost.
        storage.bucket_for(SECONDARY).objects[storage.key(job.storage_key)] = b"orphan"
        raise OSError("lost response")

    storage.copy = failed
    asyncio.run(run_jobs(use_cases, no_webhooks))
    job = repo._object_copies.values()[0]
    assert job.status == "error" and len(repo.get_object(old.id).instances) == 1
    replacement = registered(use_cases)
    storage.bucket_for(SECONDARY).objects[storage.key(replacement.storage_key)] = (
        b"replacement"
    )
    job.updated -= timedelta(seconds=31)
    repo.save_object_copy(job)
    asyncio.run(run_jobs(use_cases, no_webhooks))
    assert repo._object_copies[job.id].status == "done"
    assert storage.key(old.storage_key) not in storage.bucket_for(SECONDARY).objects
    assert (
        storage.bucket_for(SECONDARY).objects[storage.key(replacement.storage_key)]
        == b"replacement"
    )
    assert len(repo.get_object(replacement.id).instances) == 1


def test_crash_after_physical_copy_retries_without_copying_init_object():
    use_cases = make_use_cases(backends=BACKENDS)
    repo = use_cases.repository
    record = registered(use_cases)
    init = replace(record, id="init-object", storage_key=uuid4().hex)
    repo.save_object(init)
    record.init_object_id = init.id
    repo.save_object(record)
    enqueue(use_cases)
    emulate_copy(use_cases)
    job = repo._object_copies.values()[0]
    asyncio.run(use_cases.object_storage.copy(job))
    job.status = "started"
    repo.save_object_copy(job)
    asyncio.run(run_jobs(use_cases, no_webhooks))
    assert len(repo.get_object(record.id).instances) == 2
    assert len(repo.get_object(init.id).instances) == 1
    assert repo._object_copies[job.id].status == "done"


def test_registry_identity_and_current_metadata_survive_restart(tmp_path):
    path = tmp_path / "store.sqlite"
    use_cases = make_use_cases(path, BACKENDS)
    record = registered(use_cases)
    backends = use_cases.repository.list_storage_backends()
    use_cases.repository.storage.connection.close()
    with pytest.raises(ConfigurationError):
        SQLiteRepository(SQLiteStorage(path), backends[:1])
    with pytest.raises(ConfigurationError):
        SQLiteRepository(
            SQLiteStorage(path),
            [replace(backends[0], bucket_name="different"), backends[1]],
        )
    updated = replace(backends[0], label="new label", tags={"site": ["one"]})
    repo = SQLiteRepository(SQLiteStorage(path), [updated, backends[1]])
    assert repo.get_object(record.id).instances[0].storage_backend.tags == {
        "site": ["one"]
    }
    assert repo.get_object(record.id).instances[0].label == "new label"


@pytest.mark.parametrize(
    "field,value",
    [
        ("storage_id", str(PRIMARY_BACKEND_ID)),
        ("bucket_name", "primary-media"),
        ("label", "cloudflare:r2"),
        ("default_storage", True),
        ("bucket_name", "bad/name"),
    ],
)
def test_invalid_registry_rejected(field, value):
    configured = deepcopy(BACKENDS)
    configured["MEDIA_SECONDARY"][field] = value
    with pytest.raises((ValueError, ConfigurationError)):
        load_backends(
            SimpleNamespace(
                STORAGE_BACKENDS=json.dumps(configured),
                R2_ENDPOINT="https://objects.example.test",
            )
        )


def test_native_copy_signs_source_conditions_and_checks_response(monkeypatch):
    use_cases = make_use_cases(backends=BACKENDS)
    record = registered(use_cases)
    enqueue(use_cases)
    job = use_cases.repository._object_copies.values()[0]
    bodies = [
        b"<Error><Code>InternalError</Code></Error>",
        b'<CopyObjectResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><ETag>"test-etag"</ETag></CopyObjectResult>',
    ]

    async def fetch(url, **kwargs):
        assert kwargs["redirect"] == "manual"
        assert urlsplit(url).path.startswith("/secondary-media/")
        assert kwargs["headers"]["x-amz-copy-source-if-match"] == '"test-etag"'
        assert kwargs["headers"]["x-amz-copy-source"].startswith("/primary-media/")
        assert (
            "x-amz-copy-source-if-match"
            in parse_qs(urlsplit(url).query)["X-Amz-SignedHeaders"][0]
        )
        use_cases.object_storage.bucket_for(SECONDARY).objects[
            use_cases.object_storage.key(record.storage_key)
        ] = b"exact bytes"
        chunks = [bodies.pop(0)]

        async def read():
            if not chunks:
                return SimpleNamespace(done=True)
            data = chunks.pop()
            return SimpleNamespace(
                done=False,
                value=SimpleNamespace(byteLength=len(data), to_bytes=lambda: data),
            )

        async def cancel():
            pass

        reader = SimpleNamespace(read=read, cancel=cancel, releaseLock=lambda: None)
        return SimpleNamespace(
            status=200, body=SimpleNamespace(getReader=lambda: reader)
        )

    monkeypatch.setitem(sys.modules, "workers", SimpleNamespace(fetch=fetch))
    monkeypatch.setitem(
        sys.modules,
        "js",
        SimpleNamespace(AbortSignal=SimpleNamespace(timeout=lambda ms: None)),
    )
    with pytest.raises(RuntimeError, match="successful CopyObject"):
        asyncio.run(use_cases.object_storage.copy(job))
    asyncio.run(use_cases.object_storage.copy(job))
    for xml in (b"<CopyObjectResult/>", b"<Error/>"):
        with pytest.raises(RuntimeError):
            copy_etag(xml)
