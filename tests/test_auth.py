from uuid import uuid4

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from tamoss.auth import Identity, OAUTH2_ROUTE_SCOPE_GROUPS
from tests.tams.support import video_flow_payload


def test_every_api_route_has_an_explicit_permission(tamoss_app):
    routes = {
        (method, route.path)
        for route in iter_route_contexts(tamoss_app.routes)
        for method in route.methods
    }
    assert routes == set(OAUTH2_ROUTE_SCOPE_GROUPS)


@pytest.fixture
def scoped_client(tamoss_app):
    app = tamoss_app

    @app.middleware("http")
    async def verified_identity(request, call_next):
        # Test the ASGI boundary. Public headers never populate identity in production.
        role = request.headers.get("x-test-scope")
        if role is not None:
            request.state.tamoss_identity = Identity(
                "scope-test-client", frozenset(f"tams-api/{r}" for r in role.split())
            )
        return await call_next(request)

    with TestClient(app) as client:
        yield client


def test_scopes_and_identity_are_enforced(scoped_client):
    client = scoped_client
    assert client.get("/service").status_code == 401
    assert client.get("/service", headers={"x-test-scope": ""}).status_code == 403
    client.headers["x-test-scope"] = "read"
    assert client.get("/service").status_code == 200
    assert client.head("/flows").status_code == 200
    assert client.get("/missing").status_code == 404
    assert client.patch("/service").status_code == 405
    flow, source = uuid4(), uuid4()
    payload = video_flow_payload(flow, source)
    denied = client.put(f"/flows/{flow}", json=payload)
    assert denied.status_code == 403
    assert "insufficient_scope" in denied.headers["www-authenticate"]
    assert client.post("/service", json={}).status_code == 403
    assert client.delete(f"/service/webhooks/{uuid4()}").status_code == 403
    assert client.get("/flow-delete-requests").status_code == 403
    client.headers["x-test-scope"] = "read write"
    created = client.put(f"/flows/{flow}", json=payload)
    assert created.status_code == 201
    assert created.json()["created_by"] == "scope-test-client"
    assert client.delete(f"/flows/{flow}").status_code == 403
    assert client.put(f"/flows/{flow}/read_only", json=True).status_code == 403
    assert (
        client.put(f"/flows/{flow}", json={**payload, "read_only": True}).status_code
        == 403
    )
    assert client.get(f"/flows/{flow}").json()["read_only"] is False
    client.headers["x-test-scope"] = "delete"
    assert client.get("/flows").status_code == 403
    assert client.get("/flow-delete-requests").status_code == 403
    assert client.get(f"/flow-delete-requests/{uuid4()}").status_code == 404
    assert client.delete(f"/flows/{flow}").status_code == 204
    client.headers["x-test-scope"] = "admin"
    assert client.get("/flow-delete-requests").status_code == 200


def test_new_routes_fail_closed(scoped_client):
    @scoped_client.app.get("/new-route")
    async def new_route():
        return {}

    assert (
        scoped_client.get("/new-route", headers={"x-test-scope": "admin"}).status_code
        == 403
    )
