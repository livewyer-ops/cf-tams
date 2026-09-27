"""Verified client identities and BBC TAMS coarse-grained permissions."""

from dataclasses import dataclass

from fastapi import Request

from tamoss.errors import InsufficientScope, Unauthorized


_ADMIN_SCOPE = "admin"
_READ_SCOPE = "read"
_WRITE_SCOPE = "write"
_DELETE_SCOPE = "delete"
_ANY_API_SCOPE = frozenset({_ADMIN_SCOPE, _READ_SCOPE, _WRITE_SCOPE, _DELETE_SCOPE})
_READ_API_SCOPE = frozenset({_ADMIN_SCOPE, _READ_SCOPE})
_WRITE_API_SCOPE = frozenset({_ADMIN_SCOPE, _WRITE_SCOPE})
_DELETE_API_SCOPE = frozenset({_ADMIN_SCOPE, _DELETE_SCOPE})
_ADMIN_API_SCOPE = frozenset({_ADMIN_SCOPE})

OAUTH2_ROUTE_SCOPE_GROUPS: dict[tuple[str, str], frozenset[str]] = {
    ("GET", "/openapi.json"): _ANY_API_SCOPE,
    ("HEAD", "/openapi.json"): _ANY_API_SCOPE,
    ("GET", "/"): _ANY_API_SCOPE,
    ("HEAD", "/"): _ANY_API_SCOPE,
    ("GET", "/service"): _ANY_API_SCOPE,
    ("HEAD", "/service"): _ANY_API_SCOPE,
    ("POST", "/service"): _ADMIN_API_SCOPE,
    ("GET", "/service/storage-backends"): _ANY_API_SCOPE,
    ("HEAD", "/service/storage-backends"): _ANY_API_SCOPE,
    ("GET", "/service/profiles"): _READ_API_SCOPE,
    ("HEAD", "/service/profiles"): _READ_API_SCOPE,
    ("GET", "/service/profiles/{profileId}"): _READ_API_SCOPE,
    ("HEAD", "/service/profiles/{profileId}"): _READ_API_SCOPE,
    ("POST", "/service/profiles/{profileId}"): _WRITE_API_SCOPE,
    ("GET", "/service/webhooks"): _READ_API_SCOPE,
    ("HEAD", "/service/webhooks"): _READ_API_SCOPE,
    ("POST", "/service/webhooks"): _WRITE_API_SCOPE,
    ("GET", "/service/webhooks/{webhookId}"): _READ_API_SCOPE,
    ("HEAD", "/service/webhooks/{webhookId}"): _READ_API_SCOPE,
    ("PUT", "/service/webhooks/{webhookId}"): _WRITE_API_SCOPE,
    ("DELETE", "/service/webhooks/{webhookId}"): _WRITE_API_SCOPE,
    ("GET", "/flow-delete-requests"): _ADMIN_API_SCOPE,
    ("HEAD", "/flow-delete-requests"): _ADMIN_API_SCOPE,
    ("GET", "/flow-delete-requests/{request_id}"): _DELETE_API_SCOPE,
    ("HEAD", "/flow-delete-requests/{request_id}"): _DELETE_API_SCOPE,
    ("GET", "/sources"): _READ_API_SCOPE,
    ("HEAD", "/sources"): _READ_API_SCOPE,
    ("GET", "/sources/{sourceId}"): _READ_API_SCOPE,
    ("HEAD", "/sources/{sourceId}"): _READ_API_SCOPE,
    ("GET", "/sources/{sourceId}/label"): _READ_API_SCOPE,
    ("HEAD", "/sources/{sourceId}/label"): _READ_API_SCOPE,
    ("PUT", "/sources/{sourceId}/label"): _WRITE_API_SCOPE,
    ("DELETE", "/sources/{sourceId}/label"): _WRITE_API_SCOPE,
    ("GET", "/sources/{sourceId}/description"): _READ_API_SCOPE,
    ("HEAD", "/sources/{sourceId}/description"): _READ_API_SCOPE,
    ("PUT", "/sources/{sourceId}/description"): _WRITE_API_SCOPE,
    ("DELETE", "/sources/{sourceId}/description"): _WRITE_API_SCOPE,
    ("GET", "/sources/{sourceId}/tags"): _READ_API_SCOPE,
    ("HEAD", "/sources/{sourceId}/tags"): _READ_API_SCOPE,
    ("GET", "/sources/{sourceId}/tags/{name:path}"): _READ_API_SCOPE,
    ("HEAD", "/sources/{sourceId}/tags/{name:path}"): _READ_API_SCOPE,
    ("PUT", "/sources/{sourceId}/tags/{name:path}"): _WRITE_API_SCOPE,
    ("DELETE", "/sources/{sourceId}/tags/{name:path}"): _WRITE_API_SCOPE,
    ("GET", "/flows"): _READ_API_SCOPE,
    ("HEAD", "/flows"): _READ_API_SCOPE,
    ("GET", "/flows/{flowId}"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}"): _READ_API_SCOPE,
    ("PUT", "/flows/{flowId}"): _WRITE_API_SCOPE,
    ("DELETE", "/flows/{flowId}"): _DELETE_API_SCOPE,
    ("GET", "/flows/{flowId}/flow_collection"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/flow_collection"): _READ_API_SCOPE,
    ("PUT", "/flows/{flowId}/flow_collection"): _WRITE_API_SCOPE,
    ("DELETE", "/flows/{flowId}/flow_collection"): _WRITE_API_SCOPE,
    ("GET", "/flows/{flowId}/label"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/label"): _READ_API_SCOPE,
    ("PUT", "/flows/{flowId}/label"): _WRITE_API_SCOPE,
    ("DELETE", "/flows/{flowId}/label"): _WRITE_API_SCOPE,
    ("GET", "/flows/{flowId}/description"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/description"): _READ_API_SCOPE,
    ("PUT", "/flows/{flowId}/description"): _WRITE_API_SCOPE,
    ("DELETE", "/flows/{flowId}/description"): _WRITE_API_SCOPE,
    ("GET", "/flows/{flowId}/avg_bit_rate"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/avg_bit_rate"): _READ_API_SCOPE,
    ("PUT", "/flows/{flowId}/avg_bit_rate"): _WRITE_API_SCOPE,
    ("DELETE", "/flows/{flowId}/avg_bit_rate"): _WRITE_API_SCOPE,
    ("GET", "/flows/{flowId}/max_bit_rate"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/max_bit_rate"): _READ_API_SCOPE,
    ("PUT", "/flows/{flowId}/max_bit_rate"): _WRITE_API_SCOPE,
    ("DELETE", "/flows/{flowId}/max_bit_rate"): _WRITE_API_SCOPE,
    ("GET", "/flows/{flowId}/read_only"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/read_only"): _READ_API_SCOPE,
    ("PUT", "/flows/{flowId}/read_only"): _ADMIN_API_SCOPE,
    ("GET", "/flows/{flowId}/tags"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/tags"): _READ_API_SCOPE,
    ("GET", "/flows/{flowId}/tags/{name:path}"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/tags/{name:path}"): _READ_API_SCOPE,
    ("PUT", "/flows/{flowId}/tags/{name:path}"): _WRITE_API_SCOPE,
    ("DELETE", "/flows/{flowId}/tags/{name:path}"): _WRITE_API_SCOPE,
    ("POST", "/flows/{flowId}/storage"): _WRITE_API_SCOPE,
    ("GET", "/flows/{flowId}/segments"): _READ_API_SCOPE,
    ("HEAD", "/flows/{flowId}/segments"): _READ_API_SCOPE,
    ("POST", "/flows/{flowId}/segments"): _WRITE_API_SCOPE,
    ("DELETE", "/flows/{flowId}/segments"): _DELETE_API_SCOPE,
    ("GET", "/objects/{objectId:path}"): _READ_API_SCOPE,
    ("HEAD", "/objects/{objectId:path}"): _READ_API_SCOPE,
    ("POST", "/objects/{objectId:path}/instances"): _WRITE_API_SCOPE,
    ("DELETE", "/objects/{objectId:path}/instances"): _WRITE_API_SCOPE,
}


@dataclass(frozen=True)
class Identity:
    subject: str
    scopes: frozenset[str]
    method: str = "bearer-oauth2"


def identify_request(request: Request) -> Identity:
    identity = getattr(request.state, "tamoss_identity", None)
    if not isinstance(identity, Identity):
        raise Unauthorized("A valid Bearer token is required.")
    return identity


def authorize_request(request: Request) -> None:
    identity = identify_request(request)
    route = request.scope.get("route")
    groups = OAUTH2_ROUTE_SCOPE_GROUPS.get((request.method, getattr(route, "path", "")))
    if groups is None or identity.scopes.isdisjoint(
        f"tams-api/{group}" for group in groups
    ):
        raise InsufficientScope("The token does not grant access to this operation.")
