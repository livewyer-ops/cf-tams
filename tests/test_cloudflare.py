import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from botocore.auth import S3SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

from cf_tams.jobs import next_alarm, run_jobs
from cf_tams.repository import SQLiteRepository, bound_key
from cf_tams.signing import presign
from tamoss.domain.model import (
    DeletionRequestRecord,
    FlowRecord,
    MediaObjectRecord,
    ObjectCleanupRecord,
    WebhookDeliveryRecord,
    WebhookRecord,
    utc_now,
)
from tests.conftest import SQLiteStorage, make_use_cases
from tests.tams.support import (
    allocate_objects,
    create_video_flow,
    register_segment,
    upload_allocated_object,
    webhook_payload,
)


@pytest.mark.parametrize(
    "method,headers",
    [("GET", {}), ("PUT", {"Content-Type": "video/mp2t", "If-None-Match": "*"})],
)
def test_signatures_match_aws_sdk(monkeypatch, method, headers):
    now = datetime(2026, 9, 22, 12, 30, tzinfo=UTC)
    monkeypatch.setattr("botocore.auth.get_current_datetime", lambda: now)
    url = "https://1234567890abcdef.r2.cloudflarestorage.com/media/store/object"
    request = AWSRequest(method=method, url=url, headers=headers)
    S3SigV4QueryAuth(Credentials("key", "secret"), "s3", "auto", expires=3600).add_auth(
        request
    )
    actual = presign(method, url, "key", "secret", headers=headers, now=now)
    assert parse_qs(urlsplit(actual).query) == parse_qs(urlsplit(request.url).query)
    with pytest.raises(ValueError):
        presign(method, url, "key", "secret", expires=604801)


def test_sqlite_rollback_and_restart(tmp_path):
    use_cases = make_use_cases(tmp_path / "store.sqlite")
    repo = use_cases.repository
    flow = FlowRecord(uuid4(), {}, None, None, None)

    def failed():
        repo.save_flow(flow)
        raise ValueError("injected failure")

    with pytest.raises(ValueError):
        repo.atomic(failed)
    assert repo.get_flow(flow.id) is None
    repo.atomic(lambda: repo.save_flow(flow))
    repo.storage.connection.close()
    reopened = SQLiteRepository(
        SQLiteStorage(tmp_path / "store.sqlite"), repo.list_storage_backends()
    )
    assert reopened.get_flow(flow.id) == flow
    assert sorted([bound_key(10**22 + 1), bound_key(-(10**22)), bound_key(10**22)]) == [
        bound_key(-(10**22)),
        bound_key(10**22),
        bound_key(10**22 + 1),
    ]
    reopened.storage.connection.close()


def test_failed_allocation_rolls_back_every_object(client, tamoss_app):
    flow, _, _ = create_video_flow(client)
    assert (
        client.post(
            f"/flows/{flow}/storage", json={"object_ids": ["existing"]}
        ).status_code
        == 201
    )
    response = client.post(
        f"/flows/{flow}/storage", json={"object_ids": ["new", "existing"]}
    )
    assert response.status_code == 400
    assert tamoss_app.state.tamoss_use_cases.repository.get_object("new") is None


def test_accepted_flow_deletion_blocks_registration_and_allocation(client, tamoss_app):
    flow, _, _ = create_video_flow(client)
    register_segment(client, flow)
    assert client.delete(f"/flows/{flow}").status_code == 202
    response = client.post(
        f"/flows/{flow}/segments",
        json={
            "object_id": "late",
            "timerange": "[20:0_21:0)",
            "get_urls": [{"url": "https://example.com/late", "label": "external"}],
        },
    )
    assert response.status_code == 403
    assert client.post(f"/flows/{flow}/storage", json={"limit": 1}).status_code == 403
    repo = tamoss_app.state.tamoss_use_cases.repository
    assert repo.get_object("late") is None
    tamoss_app.state.process_jobs()
    assert repo.get_flow(flow) is None and repo.list_segments(flow) == []


def test_cleanup_recovers_after_failure_and_cannot_delete_reallocated_id():
    use_cases = make_use_cases()
    repo, storage = use_cases.repository, use_cases.object_storage
    old = MediaObjectRecord("reused", created=utc_now() - timedelta(days=2))
    replacement = MediaObjectRecord("reused")
    cleanup = ObjectCleanupRecord(
        uuid4(),
        old.id,
        repo.default_storage_backend().id,
        "pending",
        object_created=old.created,
        storage_key=old.storage_key,
    )
    repo.atomic(lambda: repo.save_object_cleanup(cleanup))
    repo.atomic(lambda: repo.save_object(replacement))
    storage.bucket.objects.update(
        {
            storage.key(old.storage_key): b"old",
            storage.key(replacement.storage_key): b"new",
        }
    )
    storage.bucket.fail_delete = True

    async def send(*args):
        raise AssertionError("No webhook expected")

    asyncio.run(run_jobs(use_cases, send))
    saved = repo.list_object_cleanups()[0]
    assert saved.status == "error" and next_alarm(repo) is not None
    saved.updated -= timedelta(seconds=31)
    repo.atomic(lambda: repo.save_object_cleanup(saved))
    storage.bucket.fail_delete = False
    asyncio.run(run_jobs(use_cases, send))
    assert repo.list_object_cleanups()[0].status == "done"
    assert storage.key(old.storage_key) not in storage.bucket.objects
    assert storage.bucket.objects[storage.key(replacement.storage_key)] == b"new"
    assert repo.get_object(replacement.id).storage_key == replacement.storage_key


def test_future_cleanup_cannot_starve_ready_work():
    use_cases = make_use_cases()
    repo = use_cases.repository
    for i in range(51):
        cleanup = ObjectCleanupRecord(
            uuid4(),
            str(i),
            repo.default_storage_backend().id,
            "pending",
            object_created=utc_now(),
            storage_key=str(i),
        )
        repo.save_object_cleanup(cleanup)
    ready = ObjectCleanupRecord(
        uuid4(),
        "ready",
        repo.default_storage_backend().id,
        "pending",
        object_created=utc_now() - timedelta(days=2),
        storage_key="ready",
    )
    repo.save_object_cleanup(ready)
    assert [item.id for item in repo.pending("object_cleanups", {"pending"})] == [
        ready.id
    ]


@pytest.mark.parametrize("body", [b"", b"{", b"null"])
def test_segment_body_validation_preserves_metadata(client, body):
    flow, _, _ = create_video_flow(client)
    register_segment(client, flow)
    before = client.get(f"/flows/{flow}/segments", params={"presigned": "false"}).json()
    response = client.post(
        f"/flows/{flow}/segments",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["type"] == "bad_request"
    assert (
        client.get(f"/flows/{flow}/segments", params={"presigned": "false"}).json()
        == before
    )


@pytest.mark.parametrize("delete_flow", [False, True])
def test_metadata_deletion_finishes_before_physical_cleanup(
    client, tamoss_app, delete_flow
):
    flow, _, _ = create_video_flow(client)
    other, _, _ = create_video_flow(client)
    orphan = register_segment(client, flow)
    shared = register_segment(client, flow, timerange="[10:0_20:0)")
    assert (
        client.post(
            f"/flows/{other}/segments",
            json={"object_id": shared, "timerange": "[10:0_20:0)"},
        ).status_code
        == 201
    )
    use_cases = tamoss_app.state.tamoss_use_cases
    repo, storage = use_cases.repository, use_cases.object_storage
    orphan_key = storage.key(repo.get_object(orphan).storage_key)
    response = (
        client.delete(f"/flows/{flow}")
        if delete_flow
        else client.delete(
            f"/flows/{flow}/segments", params={"timerange": "[0:0_10:0)"}
        )
    )
    assert response.status_code == 202

    async def send(*args):
        raise AssertionError("No webhook expected")

    asyncio.run(run_jobs(use_cases, send))
    request = client.get(response.headers["location"]).json()
    assert request["status"] == "done" and "timerange_remaining" not in request
    assert client.get(f"/flows/{flow}").status_code == (404 if delete_flow else 200)
    assert [s["object_id"] for s in client.get(f"/flows/{flow}/segments").json()] == (
        [] if delete_flow else [shared]
    )
    assert repo.get_object(orphan) is None
    assert other in repo.get_object(shared).referenced_by_flows
    assert orphan_key in storage.bucket.objects
    assert [(c.object_id, c.status) for c in repo.list_object_cleanups()] == [
        (orphan, "pending")
    ]
    assert next_alarm(repo) > int((utc_now() + timedelta(hours=23)).timestamp() * 1000)


def test_deletion_completion_is_atomic_and_waits_for_all_batches(
    client, tamoss_app, monkeypatch
):
    monkeypatch.setattr(
        "tamoss.application.contexts.deletion_processor.DELETE_SEGMENT_BATCH_SIZE", 1
    )
    flow, _, _ = create_video_flow(client)
    register_segment(client, flow)
    register_segment(client, flow, timerange="[10:0_20:0)")
    response = client.delete(f"/flows/{flow}")
    use_cases = tamoss_app.state.tamoss_use_cases
    repo = use_cases.repository

    async def send(*args):
        raise AssertionError("No webhook expected")

    def fail(cleanup):
        raise OSError("injected cleanup persistence failure")

    with monkeypatch.context() as patch:
        patch.setattr(repo, "save_object_cleanup", fail)
        asyncio.run(run_jobs(use_cases, send))
    failed = client.get(response.headers["location"]).json()
    assert failed["status"] == "error"
    assert len(client.get(f"/flows/{flow}/segments").json()) == 2
    assert repo.list_object_cleanups() == []
    request = repo.get_delete_request(UUID(response.json()["id"]))
    request.updated -= timedelta(seconds=31)
    repo.save_delete_request(request)
    asyncio.run(run_jobs(use_cases, send))
    assert client.get(response.headers["location"]).json()["status"] == "started"
    assert len(client.get(f"/flows/{flow}/segments").json()) == 1
    asyncio.run(run_jobs(use_cases, send))
    assert client.get(response.headers["location"]).json()["status"] == "done"
    assert client.get(f"/flows/{flow}").status_code == 404
    assert len(repo.list_object_cleanups()) == 2


def test_cleanup_survives_restart_and_completed_request_expiry(tmp_path):
    path = tmp_path / "cleanup.sqlite"
    use_cases = make_use_cases(path)
    repo, storage = use_cases.repository, use_cases.object_storage
    request = DeletionRequestRecord(uuid4(), uuid4(), "[0:0_1:0)", True, "started")
    cleanup = ObjectCleanupRecord(
        uuid4(),
        "removed",
        repo.default_storage_backend().id,
        "pending",
        object_created=utc_now(),
        storage_key="old-allocation",
        delete_request_id=request.id,
    )
    repo.save_delete_request(request)
    repo.save_object_cleanup(cleanup)
    assert next_alarm(repo) < int((utc_now() + timedelta(seconds=5)).timestamp() * 1000)
    repo.storage.connection.close()
    use_cases = make_use_cases(path)
    repo, storage = use_cases.repository, use_cases.object_storage
    storage.bucket.objects[storage.key(cleanup.storage_key)] = b"retained"

    async def send(*args):
        raise AssertionError("No webhook expected")

    asyncio.run(run_jobs(use_cases, send))
    request = repo.get_delete_request(request.id)
    assert request.status == "done"
    assert repo.list_object_cleanups()[0].status == "pending"
    request.updated -= timedelta(days=2)
    repo.save_delete_request(request)
    asyncio.run(run_jobs(use_cases, send))
    assert repo.get_delete_request(request.id) is None
    assert repo.list_object_cleanups()[0].status == "pending"
    cleanup.object_created -= timedelta(days=2)
    repo.save_object_cleanup(cleanup)
    storage.bucket.fail_delete = True
    asyncio.run(run_jobs(use_cases, send))
    cleanup = repo.list_object_cleanups()[0]
    assert cleanup.status == "error" and next_alarm(repo) is not None
    cleanup.updated -= timedelta(seconds=31)
    repo.save_object_cleanup(cleanup)
    storage.bucket.fail_delete = False
    asyncio.run(run_jobs(use_cases, send))
    assert repo.list_object_cleanups()[0].status == "done"
    assert storage.bucket.objects == {}
    repo.storage.connection.close()


@pytest.mark.parametrize("retry_status", [401, 403, 503])
def test_webhook_retry_and_idle_alarm(retry_status):
    use_cases = make_use_cases()
    repo = use_cases.repository
    assert next_alarm(repo) is None
    hook = WebhookRecord(
        uuid4(),
        {"url": "https://example.com/hook", "events": ["flows/created"]},
        "started",
    )
    repo.save_webhook(hook)
    delivery = WebhookDeliveryRecord(
        uuid4(),
        hook.id,
        hook.data,
        "flows/created",
        utc_now(),
        {"event_type": "flows/created"},
        "pending",
    )
    repo.save_webhook_delivery(delivery)
    calls = []

    async def send(url, headers, body):
        calls.append(url)
        return retry_status if len(calls) == 1 else 204

    asyncio.run(run_jobs(use_cases, send))
    saved = repo.get_webhook_delivery(delivery.id)
    assert saved.status == "pending" and saved.attempt_count == 1
    asyncio.run(run_jobs(use_cases, send))
    assert len(calls) == 1
    saved.next_attempt_at = utc_now() - timedelta(seconds=1)
    repo.save_webhook_delivery(saved)
    asyncio.run(run_jobs(use_cases, send))
    assert repo.get_webhook_delivery(delivery.id).status == "done"
    assert len(calls) == 2


def test_failed_segment_does_not_bind_media_to_its_init_object(client, tamoss_app):
    flow, _, _ = create_video_flow(
        client,
        essence_parameters={
            "frame_width": 1920,
            "frame_height": 1080,
            "frame_rate": {"numerator": 25, "denominator": 1},
            "init_segments": True,
        },
    )
    allocate_objects(client, flow, ["media", "rejected-init", "accepted-init"])
    for object_id in ("media", "rejected-init", "accepted-init"):
        upload_allocated_object(client, object_id)
    response = client.post(
        f"/flows/{flow}/segments",
        json=[
            {
                "object_id": "media",
                "init_object_id": "rejected-init",
                "timerange": "[0:0_10:0)",
                "object_timerange": "[0:0_5:0)",
            },
            {
                "object_id": "media",
                "init_object_id": "accepted-init",
                "timerange": "[0:0_10:0)",
            },
        ],
    )
    assert response.status_code == 200
    assert len(response.json()["failed_segments"]) == 1
    assert len(client.get(f"/flows/{flow}/segments").json()) == 1
    repo = tamoss_app.state.tamoss_use_cases.repository
    assert repo.get_object("media").init_object_id == "accepted-init"
    assert repo.get_object("accepted-init").referenced_by_flows == {flow}
    assert repo.get_object("rejected-init").referenced_by_flows == set()


def test_flow_deletion_emits_segment_events_before_flow_event(
    client, tamoss_app, monkeypatch
):
    monkeypatch.setattr(
        "tamoss.application.contexts.deletion_processor.DELETE_SEGMENT_BATCH_SIZE", 1
    )
    flow, _, _ = create_video_flow(client)
    register_segment(client, flow, timerange="[0:0_10:0)")
    register_segment(client, flow, timerange="[10:0_20:0)")
    hook = client.post(
        "/service/webhooks",
        json=webhook_payload(events=["flows/segments_deleted", "flows/deleted"]),
    )
    assert hook.status_code == 201
    assert client.delete(f"/flows/{flow}").status_code == 202
    tamoss_app.state.process_jobs()
    repo = tamoss_app.state.tamoss_use_cases.repository
    for delivery in repo.list_webhook_deliveries():
        delivery.next_attempt_at = utc_now() - timedelta(seconds=1)
        repo.save_webhook_delivery(delivery)
    tamoss_app.state.process_jobs()
    deliveries = sorted(
        repo.list_webhook_deliveries(), key=lambda delivery: delivery.event_timestamp
    )
    assert all(delivery.status == "done" for delivery in deliveries)
    events = [delivery.payload for delivery in deliveries]
    assert [event["event_type"] for event in events] == [
        "flows/segments_deleted",
        "flows/segments_deleted",
        "flows/deleted",
    ]
    assert [event["event"]["timerange"] for event in events[:2]] == [
        "[0:0_10:0)",
        "[10:0_20:0)",
    ]
