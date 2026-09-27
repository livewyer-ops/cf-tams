# Adapted from TAMOSS RC3 conformance regressions.
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.tams.support import (
    allocate_objects,
    create_video_flow,
    register_segment,
    upload_allocated_object,
)

pytestmark = [pytest.mark.tams_conformance, pytest.mark.tams_semantics]


@pytest.mark.parametrize(
    "timerange",
    [
        "(1000000000:1)",
        "[1000000000:1)",
        "(1000000000:1]",
        "[1.5_2.5)",
        "[01:0_2:0)",
        "[0:1000000000_2:0)",
    ],
)
@pytest.mark.parametrize(
    "method,resource",
    [
        ("GET", "flows"),
        ("HEAD", "flows"),
        ("GET", "flow"),
        ("HEAD", "flow"),
        ("GET", "segments"),
        ("HEAD", "segments"),
        ("DELETE", "segments"),
    ],
)
def test_exclusive_instant_is_rejected_before_selection(
    client: TestClient, timerange: str, method: str, resource: str
) -> None:
    # schemas/timerange.json description forbids exclusive instantaneous ranges.
    flow_id, _, _ = create_video_flow(client)
    register_segment(client, flow_id, timerange="[1000000000:1_1000000000:2)")
    path = {
        "flows": "/flows",
        "flow": f"/flows/{flow_id}",
        "segments": f"/flows/{flow_id}/segments",
    }[resource]
    response = client.request(method, path, params={"timerange": timerange})
    assert response.status_code == 400
    assert len(client.get(f"/flows/{flow_id}/segments").json()) == 1


@pytest.mark.parametrize(
    "timerange,count",
    [
        ("[1000000000:1]", 1),
        ("[1000000000:1_1000000000:1]", 1),
        ("(1000000000:1_1000000000:2)", 0),
        ("()", 0),
        ("_", 1),
        ("[1000000000:2_", 0),
    ],
)
def test_valid_timerange_controls_preserve_clusivity(
    client: TestClient, timerange: str, count: int
) -> None:
    flow_id, _, _ = create_video_flow(client)
    register_segment(client, flow_id, timerange="[1000000000:1_1000000000:2)")
    response = client.get(f"/flows/{flow_id}/segments", params={"timerange": timerange})
    assert response.status_code == 200
    assert len(response.json()) == count


@pytest.mark.parametrize("field", ["timerange", "object_timerange"])
@pytest.mark.parametrize("bulk", [False, True])
def test_segment_body_rejects_exclusive_instant(
    client: TestClient, field: str, bulk: bool
) -> None:
    flow_id, _, _ = create_video_flow(client)
    object_id = f"instant/{flow_id}.ts"
    allocate_objects(client, flow_id, [object_id])
    upload_allocated_object(client, object_id)
    segment = {"object_id": object_id, "timerange": "[1:0_2:0)", field: "(1:0)"}
    response = client.post(
        f"/flows/{flow_id}/segments", json=[segment] if bulk else segment
    )
    if bulk:
        assert response.status_code == 200
        assert len(response.json()["failed_segments"]) == 1
    else:
        assert response.status_code == 400
    assert client.get(f"/flows/{flow_id}/segments").json() == []


@pytest.mark.parametrize("reused", [False, True])
@pytest.mark.parametrize(
    ("timerange", "offset", "expected"),
    [
        ("[0:0_10:0)", None, 201),
        ("[2:0_8:0)", None, 201),
        ("[100:0_110:0)", "100:0", 201),
        ("[-10:0_0:0)", "-10:0", 201),
        ("[10:0_20:0)", None, 400),
        ("[-1:0_9:0)", None, 400),
        ("[0:0_10:0]", None, 400),
        ("[9:0]", None, 201),
        ("[10:0]", None, 400),
    ],
)
def test_segment_range_must_fit_object_after_timestamp_offset(
    client: TestClient, reused: bool, timerange: str, offset: str | None, expected: int
) -> None:
    original_flow_id, _, _ = create_video_flow(client)
    object_id = str(uuid4())
    if reused:
        register_segment(client, original_flow_id, object_id=object_id)
    target_flow_id, _, _ = create_video_flow(client)
    payload = {"object_id": object_id, "timerange": timerange}
    if offset is not None:
        payload["ts_offset"] = offset
    if not reused:
        payload.update(
            object_timerange="[0:0_10:0)",
            get_urls=[{"url": "https://media.example/object.ts", "label": "external"}],
        )
    response = client.post(f"/flows/{target_flow_id}/segments", json=payload)
    assert response.status_code == expected, response.text
    if expected == 400:
        assert client.get(f"/flows/{target_flow_id}/segments").json() == []
        if not reused:
            assert client.get(f"/objects/{object_id}").status_code == 404
