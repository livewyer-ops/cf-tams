"""Run with TAMS_TEST_URL against pywrangler dev or a disposable installation."""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4
from xml.etree import ElementTree

import httpx
import pytest

from tests.tams.support import video_flow_payload
from cf_tams.signing import presign

pytestmark = [
    pytest.mark.worker,
    pytest.mark.skipif(
        not os.getenv("TAMS_TEST_URL"),
        reason="Set TAMS_TEST_URL to exercise a running Worker",
    ),
]


@pytest.mark.skipif(
    not os.getenv("TAMS_TEST_ORIGIN"),
    reason="Set TAMS_TEST_ORIGIN to an allowed API origin",
)
def test_worker_cors():
    origin = os.environ["TAMS_TEST_ORIGIN"]
    preflight = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    with httpx.Client(base_url=os.environ["TAMS_TEST_URL"], timeout=60) as client:
        response = client.options("/service", headers=preflight)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert (
            "authorization" in response.headers["access-control-allow-headers"].lower()
        )
        for override in (
            {"Origin": "https://not-allowed.invalid"},
            {"Access-Control-Request-Method": "TRACE"},
            {"Access-Control-Request-Headers": "X-Not-Allowed"},
        ):
            assert (
                client.options("/service", headers=preflight | override).status_code
                == 400
            )
        response = client.get("/service", headers={"Origin": origin})
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["www-authenticate"] == "Bearer"
        client.headers.update(
            {"Authorization": "Bearer " + os.environ["TAMS_TEST_TOKEN"]}
        )
        for method, path, expected, content in (
            ("GET", "/service", 200, None),
            ("HEAD", "/service", 200, None),
            ("GET", "/not-a-route", 404, None),
            ("POST", "/flows/not-a-uuid/segments", 400, b"{"),
            ("POST", "/service", 413, b"x" * (4 * 1024 * 1024 + 1)),
        ):
            response = client.request(
                method,
                path,
                content=content,
                headers={"Origin": origin, "Content-Type": "application/json"},
            )
            assert response.status_code == expected
            assert response.headers["access-control-allow-origin"] == origin
            assert "origin" in response.headers["vary"].lower()
            if method == "HEAD":
                assert response.content == b""
        normal = client.get("/service")
        assert normal.status_code == 200
        assert "access-control-allow-origin" not in normal.headers
        denied = client.get(
            "/service", headers={"Origin": "https://not-allowed.invalid"}
        )
        assert denied.status_code == 200
        assert "access-control-allow-origin" not in denied.headers


def test_worker_upload_registration_concurrency_and_deletion(request):
    base = os.environ["TAMS_TEST_URL"]
    token = os.environ["TAMS_TEST_TOKEN"]
    local = urlsplit(base).hostname in {"127.0.0.1", "localhost"}
    flow, source = uuid4(), uuid4()
    object_id = f"checks/{uuid4()}/literal%2F.ts"
    body = b"0123456789" * 200000
    with httpx.Client(
        base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=60
    ) as client:
        assert httpx.get(base + "/service").status_code == 401
        assert (
            client.get("/openapi.json").json()["info"]["x-bbc-tams-api-version"]
            == "8.2"
        )
        assert (
            client.put(
                f"/flows/{flow}", json=video_flow_payload(flow, source)
            ).status_code
            == 201
        )

        def cleanup():
            response = httpx.delete(
                f"{base}/flows/{flow}", headers=client.headers, timeout=60
            )
            assert response.status_code in {202, 204, 404}

        request.addfinalizer(cleanup)
        allocation = client.post(
            f"/flows/{flow}/storage", json={"object_ids": [object_id]}
        ).json()["media_objects"][0]
        put = allocation["put_url"]
        assert parse_qs(urlsplit(put["url"]).query)["X-Amz-Expires"] == ["3600"]
        segment = {"object_id": object_id, "timerange": "[10000000000:1_10000000000:2)"}
        assert client.post(f"/flows/{flow}/segments", json=segment).status_code == 400
        if local:
            bucket, key = urlsplit(put["url"]).path.lstrip("/").split("/", 1)
            media_url = f"{base}/cdn-cgi/local/explorer/api/r2/buckets/{bucket}/objects/{quote(key, safe='')}"
            assert (
                httpx.put(
                    media_url, content=body, headers={"Content-Type": "video/mp2t"}
                ).status_code
                == 200
            )
        else:
            assert httpx.put(
                put["url"], content=body, headers=put["headers"]
            ).is_success
            assert (
                httpx.put(
                    put["url"], content=b"overwrite", headers=put["headers"]
                ).status_code
                == 412
            )

        async def register_twice():
            async with httpx.AsyncClient(
                base_url=base, headers=client.headers, timeout=60
            ) as concurrent:
                responses = await asyncio.gather(
                    *(
                        concurrent.post(f"/flows/{flow}/segments", json=segment)
                        for _ in range(2)
                    )
                )
                return sorted(r.status_code for r in responses)

        assert asyncio.run(register_twice()) == [201, 400]
        listed = client.get(f"/flows/{flow}/segments", params={"presigned": "true"})
        assert (
            len(listed.json()) == 1
            and listed.json()[0]["timerange"] == segment["timerange"]
        )
        assert client.get(f"/objects/{quote(object_id, safe='')}").status_code == 200
        assert (
            client.get(
                f"/objects/{quote(object_id.replace('%2F', '/'), safe='')}"
            ).status_code
            == 404
        )
        signed_get = listed.json()[0]["get_urls"][0]["url"]
        if local:
            assert httpx.get(media_url).content == body
        else:
            assert httpx.get(signed_get).content == body
            unsigned = httpx.get(signed_get.split("?", 1)[0])
            # R2 reports a missing Authorization header as InvalidArgument (400).
            assert unsigned.status_code in {400, 403}
            assert ElementTree.fromstring(unsigned.content).findtext("Code") in {
                "InvalidArgument",
                "AccessDenied",
            }
            ranged = httpx.get(signed_get, headers={"Range": "bytes=10-19"})
            assert ranged.status_code == 206 and ranged.content == body[10:20]
            signature = parse_qs(urlsplit(signed_get).query)["X-Amz-Signature"][0]
            tampered = httpx.get(
                signed_get.replace(signature, f"{int(signature, 16) ^ 1:064x}")
            )
            assert tampered.status_code == 403
            assert (
                ElementTree.fromstring(tampered.content).findtext("Code")
                == "SignatureDoesNotMatch"
            )
        assert (
            client.post(
                f"/flows/{flow}/storage",
                json={"object_ids": ["rollback-" + str(flow), object_id]},
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/flows/{flow}/storage", json={"object_ids": ["rollback-" + str(flow)]}
            ).status_code
            == 201
        )
        assert client.post(f"/flows/{flow}/segments").status_code == 400
        deletion = client.delete(f"/flows/{flow}")
        assert deletion.status_code == 202
        deadline = time.monotonic() + 10
        while client.get(deletion.headers["location"]).json()["status"] != "done":
            assert time.monotonic() < deadline, (
                "Deletion alarm did not complete metadata deletion"
            )
            time.sleep(0.25)
        assert client.get(f"/flows/{flow}").status_code == 404
        if local:
            # Physical cleanup waits for the 24-hour allocation lifetime.
            assert httpx.get(media_url).content == body


@pytest.mark.skipif(
    not os.getenv("R2_ACCESS_KEY_ID") or not os.getenv("R2_SECRET_ACCESS_KEY"),
    reason="Live URL expiry verification requires bucket-scoped R2 signing credentials",
)
def test_live_signed_url_expiry():
    base = os.environ["TAMS_TEST_URL"]
    if urlsplit(base).hostname in {"127.0.0.1", "localhost"}:
        pytest.skip("Local R2 does not expose the S3 presigning endpoint")
    flow, source = uuid4(), uuid4()
    with httpx.Client(
        base_url=base,
        headers={"Authorization": "Bearer " + os.environ["TAMS_TEST_TOKEN"]},
        timeout=60,
    ) as client:
        assert (
            client.put(
                f"/flows/{flow}", json=video_flow_payload(flow, source)
            ).status_code
            == 201
        )
        try:
            allocation = client.post(
                f"/flows/{flow}/storage", json={"limit": 1}
            ).json()["media_objects"][0]
            url = allocation["put_url"]["url"].split("?", 1)[0]
            expired = presign(
                "GET",
                url,
                os.environ["R2_ACCESS_KEY_ID"],
                os.environ["R2_SECRET_ACCESS_KEY"],
                expires=1,
                now=datetime.now(UTC) - timedelta(minutes=5),
            )
            assert httpx.get(expired).status_code == 403
        finally:
            assert client.delete(f"/flows/{flow}").status_code == 204


def test_live_multiple_backend_upload_copy_and_delete():
    base = os.environ["TAMS_TEST_URL"]
    if urlsplit(base).hostname in {"127.0.0.1", "localhost"}:
        pytest.skip("R2 CopyObject requires the deployed S3 endpoint")
    with httpx.Client(
        base_url=base,
        headers={"Authorization": "Bearer " + os.environ["TAMS_TEST_TOKEN"]},
        timeout=60,
    ) as client:
        backends = client.get("/service/storage-backends").json()
        if len(backends) < 2:
            pytest.skip("Configure two R2 storage backends to exercise copying")
        primary = next(backend for backend in backends if backend["default_storage"])
        secondary = next(
            backend for backend in backends if backend["id"] != primary["id"]
        )
        flow, source = uuid4(), uuid4()
        assert (
            client.put(
                f"/flows/{flow}", json=video_flow_payload(flow, source)
            ).status_code
            == 201
        )
        body = b"multi-backend-exact-byte-check\x00\xff" * 65536
        try:
            for index, backend in enumerate((primary, secondary)):
                object_id = f"multi-backend-{uuid4()}"
                payload = {"object_ids": [object_id]}
                if index:
                    payload["storage_id"] = backend["id"]
                allocation = client.post(f"/flows/{flow}/storage", json=payload)
                assert allocation.status_code == 201
                allocated = allocation.json()["media_objects"][0]
                assert allocated["storage_id"] == backend["id"]
                put = allocated["put_url"]
                assert httpx.put(
                    put["url"], headers=put["headers"], content=body, timeout=60
                ).is_success
                assert (
                    client.post(
                        f"/flows/{flow}/segments",
                        json={
                            "object_id": object_id,
                            "timerange": f"[{index}:0_{index + 1}:0)",
                        },
                    ).status_code
                    == 201
                )
                destination = secondary if index == 0 else primary
                path = f"/objects/{object_id}"
                assert (
                    client.post(
                        path + "/instances", json={"storage_id": destination["id"]}
                    ).status_code
                    == 201
                )
                deadline = time.monotonic() + 90
                while True:
                    urls = client.get(
                        path,
                        params={
                            "presigned": "true",
                            "accept_storage_ids": destination["id"],
                        },
                    ).json()["get_urls"]
                    if urls:
                        break
                    assert time.monotonic() < deadline, (
                        "The destination was not advertised after copying"
                    )
                    time.sleep(0.5)
                copied = httpx.get(urls[0]["url"], timeout=60)
                assert copied.status_code == 200 and copied.content == body
                assert copied.headers["content-type"] == put["content-type"]
                assert (
                    client.post(
                        path + "/instances", json={"storage_id": destination["id"]}
                    ).status_code
                    == 400
                )
                assert (
                    client.delete(
                        path + "/instances", params={"storage_id": backend["id"]}
                    ).status_code
                    == 204
                )
                assert (
                    client.delete(
                        path + "/instances", params={"storage_id": destination["id"]}
                    ).status_code
                    == 400
                )
                assert (
                    httpx.get(
                        client.get(path, params={"presigned": "true"}).json()[
                            "get_urls"
                        ][0]["url"],
                        timeout=60,
                    ).content
                    == body
                )
        finally:
            assert client.delete(f"/flows/{flow}").status_code in {202, 204}
