# Adapted from TAMOSS: native fetch transport and explicit webhook host allowlist.
from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import ParseResult, urlparse
from uuid import UUID, uuid4

from tamoss.application.contexts.object_get_urls import objects_get_urls
from tamoss.contract.payloads import (
    JsonPayload,
    flow_payload,
    source_payload,
    without_none,
)
from tamoss.domain.flow_collections import (
    collected_by_by_flow_id,
    collection_child_id,
    flow_collection,
    flow_with_collected_by,
)
from tamoss.domain.model import (
    FlowRecord,
    MediaObjectRecord,
    SegmentRecord,
    SourceRecord,
    WebhookDeliveryRecord,
    WebhookRecord,
    utc_now,
)
from tamoss.domain.segments import segment_object_timerange, timerange_union
from tamoss.ports.object_storage import ObjectStorage
from tamoss.ports.repositories import (
    WebhookEventRepository,
    WebhookResourceRepository,
)

VALID_WEBHOOK_EVENTS = {
    "flows/created",
    "flows/updated",
    "flows/deleted",
    "flows/segments_added",
    "flows/segments_deleted",
    "sources/created",
    "sources/updated",
    "sources/deleted",
}
RETRIABLE_STATUS_CODES = {401, 403, 408, 429, 500, 502, 503, 504}

_HTTP_FIELD_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RESERVED_WEBHOOK_HEADERS = {
    "connection",
    "content-length",
    "content-type",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_DEFAULT_RETRY_BACKOFF_SECONDS = (30, 60, 120, 300)
_CLOUD_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}
_METADATA_HOSTNAMES = {
    "metadata",
    "metadata.google.internal",
}
_WEBHOOK_CREDENTIAL_REF = "webhook.api_key_value"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookEgressPolicy:
    allow_private_targets: bool = False
    allowed_hosts: tuple[str, ...] = ()


class WebhookEgressError(ValueError):
    pass


WebhookData = Mapping[str, object]
WebhookEventFactory = Callable[[WebhookRecord], JsonPayload]
FlowWebhookEventFactory = Callable[[WebhookRecord, list[str]], JsonPayload]


class FlowEventResourceRepository(Protocol):
    def list_flows_by_source(self, source_id: UUID) -> list[FlowRecord]: ...

    def list_flows_collecting(self, flow_ids: Iterable[UUID]) -> list[FlowRecord]: ...

    def get_source(self, source_id: UUID) -> SourceRecord | None: ...


@dataclass(frozen=True)
class FlowEventContext:
    source: SourceRecord | None
    flow_collected_by_ids: list[str]
    source_collected_by_ids: list[str]


def validate_webhook_configuration(
    data: WebhookData,
    *,
    egress_policy: WebhookEgressPolicy | None = None,
) -> None:
    validate_webhook_url(data.get("url"), egress_policy=egress_policy)
    validate_api_key_header_name(data.get("api_key_name"))
    events_value = data.get("events")
    if not isinstance(events_value, list):
        raise ValueError("Unsupported webhook event")
    events = _list_of_strings(events_value)
    if any(event not in VALID_WEBHOOK_EVENTS for event in events):
        raise ValueError("Unsupported webhook event")


def validate_webhook_url(
    value: object,
    *,
    egress_policy: WebhookEgressPolicy | None = None,
) -> None:
    parsed = _parse_webhook_url(str(value) if value is not None else "")
    validate_webhook_target(parsed, egress_policy=egress_policy)


def validate_api_key_header_name(value: object) -> None:
    if value is None:
        return
    header_name = str(value)
    if (
        not header_name
        or header_name.strip() != header_name
        or not _HTTP_FIELD_NAME.fullmatch(header_name)
        or header_name.lower() in _RESERVED_WEBHOOK_HEADERS
    ):
        raise ValueError("Invalid webhook api_key_name")


def webhook_delivery_snapshot(webhook_data: WebhookData, *, status: str) -> JsonPayload:
    api_key_value_ref = None
    if webhook_data.get("api_key_name") and webhook_data.get("api_key_value"):
        api_key_value_ref = _WEBHOOK_CREDENTIAL_REF
    return {
        "url": webhook_data["url"],
        "api_key_name": webhook_data.get("api_key_name"),
        "api_key_value_ref": api_key_value_ref,
        "accept_get_urls": webhook_data.get("accept_get_urls"),
        "accept_storage_ids": webhook_data.get("accept_storage_ids"),
        "presigned": webhook_data.get("presigned"),
        "verbose_storage": webhook_data.get("verbose_storage"),
        "status": status,
    }


def webhook_for_delivery(
    webhook_snapshot: WebhookData,
    live_webhook_data: WebhookData,
) -> JsonPayload:
    webhook = dict(webhook_snapshot)
    api_key_value_ref = webhook.pop("api_key_value_ref", None)
    if api_key_value_ref == _WEBHOOK_CREDENTIAL_REF:
        webhook["api_key_value"] = live_webhook_data.get("api_key_value")
    return webhook


def webhook_matches(
    webhook_data: WebhookData,
    *,
    event_type: str,
    flow_ids: list[str],
    source_ids: list[str],
    flow_collected_by_ids: list[str],
    source_collected_by_ids: list[str],
) -> bool:
    if event_type not in _list_of_strings(webhook_data.get("events")):
        return False
    is_flow_event = event_type.startswith("flows/")
    flow_filter_matches = True
    flow_collected_filter_matches = True
    if is_flow_event:
        flow_filter_matches = _selector_matches(
            flow_ids,
            _list_of_strings(webhook_data.get("flow_ids")),
        )
        flow_collected_filter_matches = _collection_selector_matches(
            flow_collected_by_ids,
            _optional_list_of_strings(webhook_data.get("flow_collected_by_ids")),
        )
    return (
        flow_filter_matches
        and _selector_matches(
            source_ids, _list_of_strings(webhook_data.get("source_ids"))
        )
        and flow_collected_filter_matches
        and _collection_selector_matches(
            source_collected_by_ids,
            _optional_list_of_strings(webhook_data.get("source_collected_by_ids")),
        )
    )


def active_webhooks_for_event(
    repository: WebhookEventRepository,
    event_type: str,
) -> list[WebhookRecord]:
    """Active webhooks subscribed to the event type.

    Publishing call sites use this to skip event-context queries and payload
    construction entirely when nothing would be delivered.
    """
    return [
        webhook
        for webhook in repository.list_webhooks()
        if webhook.status in {"created", "started"}
        and event_type in _list_of_strings(webhook.data.get("events"))
    ]


def publish_webhook_event(
    *,
    repository: WebhookEventRepository,
    event_type: str,
    event_factory: WebhookEventFactory,
    flow: FlowRecord | None,
    source: SourceRecord | None,
    flow_collected_by_ids: list[str],
    source_collected_by_ids: list[str],
    webhooks: list[WebhookRecord] | None = None,
) -> list[WebhookDeliveryRecord]:
    event_timestamp = utc_now()
    flow_ids = [str(flow.id)] if flow is not None else []
    source_ids = [str(source.id)] if source is not None else []
    deliveries: list[WebhookDeliveryRecord] = []

    candidates = (
        webhooks
        if webhooks is not None
        else active_webhooks_for_event(repository, event_type)
    )
    for webhook in candidates:
        if not webhook_matches(
            webhook.data,
            event_type=event_type,
            flow_ids=flow_ids,
            source_ids=source_ids,
            flow_collected_by_ids=flow_collected_by_ids,
            source_collected_by_ids=source_collected_by_ids,
        ):
            continue
        if webhook.status == "created":
            webhook.status = "started"
            repository.save_webhook(webhook)

        delivery = WebhookDeliveryRecord(
            id=uuid4(),
            webhook_id=webhook.id,
            webhook_snapshot=webhook_delivery_snapshot(
                webhook.data, status=webhook.status
            ),
            event_type=event_type,
            event_timestamp=event_timestamp,
            payload={
                "event_timestamp": event_timestamp.isoformat(),
                "event_type": event_type,
                "event": event_factory(webhook),
            },
            status="pending",
            created=event_timestamp,
            updated=event_timestamp,
            next_attempt_at=event_timestamp,
        )
        repository.save_webhook_delivery(delivery)
        deliveries.append(delivery)
    return deliveries


def _publish_flow_webhook_event(
    *,
    repository: WebhookEventRepository,
    resource_repository: FlowEventResourceRepository,
    event_type: str,
    flow: FlowRecord,
    event_factory: FlowWebhookEventFactory,
    event_context: FlowEventContext | None = None,
) -> list[WebhookDeliveryRecord]:
    webhooks = active_webhooks_for_event(repository, event_type)
    if not webhooks:
        return []
    context = event_context or flow_event_context(resource_repository, flow)
    return publish_webhook_event(
        repository=repository,
        event_type=event_type,
        event_factory=lambda webhook: event_factory(
            webhook, context.flow_collected_by_ids
        ),
        flow=flow,
        source=context.source,
        flow_collected_by_ids=context.flow_collected_by_ids,
        source_collected_by_ids=context.source_collected_by_ids,
        webhooks=webhooks,
    )


def publish_flow_event(
    *,
    repository: WebhookEventRepository,
    resource_repository: FlowEventResourceRepository,
    event_type: str,
    flow: FlowRecord,
) -> list[WebhookDeliveryRecord]:
    return _publish_flow_webhook_event(
        repository=repository,
        resource_repository=resource_repository,
        event_type=event_type,
        flow=flow,
        event_factory=lambda _webhook, collected_by_ids: {
            "flow": flow_payload(flow_with_collected_by(flow, collected_by_ids))
        },
    )


def publish_flow_deleted(
    *,
    repository: WebhookEventRepository,
    resource_repository: FlowEventResourceRepository,
    flow: FlowRecord,
    event_context: FlowEventContext,
) -> list[WebhookDeliveryRecord]:
    return _publish_flow_webhook_event(
        repository=repository,
        resource_repository=resource_repository,
        event_type="flows/deleted",
        flow=flow,
        event_factory=lambda _webhook, _collected_by_ids: {"flow_id": str(flow.id)},
        event_context=event_context,
    )


def publish_segments_added(
    *,
    repository: WebhookEventRepository,
    resource_repository: FlowEventResourceRepository,
    object_storage: ObjectStorage,
    flow: FlowRecord,
    segments: list[SegmentRecord],
    objects_by_id: dict[str, MediaObjectRecord],
) -> list[WebhookDeliveryRecord]:
    return _publish_flow_webhook_event(
        repository=repository,
        resource_repository=resource_repository,
        event_type="flows/segments_added",
        flow=flow,
        event_factory=lambda webhook, _collected_by_ids: segments_added_event(
            flow=flow,
            segments=segments,
            objects_by_id=objects_by_id,
            webhook_data=webhook.data,
            object_storage=object_storage,
        ),
    )


def publish_segments_deleted(
    *,
    repository: WebhookEventRepository,
    resource_repository: FlowEventResourceRepository,
    flow: FlowRecord,
    segments: list[SegmentRecord],
) -> list[WebhookDeliveryRecord]:
    return _publish_flow_webhook_event(
        repository=repository,
        resource_repository=resource_repository,
        event_type="flows/segments_deleted",
        flow=flow,
        event_factory=lambda _webhook, _collected_by_ids: {
            "flow_id": str(flow.id),
            "timerange": timerange_union(segments),
        },
    )


def publish_source_event(
    *,
    repository: WebhookEventRepository,
    resource_repository: WebhookResourceRepository,
    event_type: str,
    source: SourceRecord,
) -> list[WebhookDeliveryRecord]:
    webhooks = active_webhooks_for_event(repository, event_type)
    if not webhooks:
        return []
    relationship = resource_repository.source_relationships_for([source.id]).get(
        source.id
    )
    return publish_webhook_event(
        repository=repository,
        event_type=event_type,
        event_factory=lambda _webhook: {
            "source": source_payload(
                source,
                source_collection=(
                    relationship.source_collection if relationship else None
                ),
                collected_by=relationship.collected_by if relationship else None,
            )
        },
        flow=None,
        source=source,
        flow_collected_by_ids=[],
        source_collected_by_ids=source_collected_by_ids(resource_repository, source),
        webhooks=webhooks,
    )


def publish_source_deleted(
    *,
    repository: WebhookEventRepository,
    source: SourceRecord,
    source_collected_by_ids: list[str],
) -> list[WebhookDeliveryRecord]:
    webhooks = active_webhooks_for_event(repository, "sources/deleted")
    if not webhooks:
        return []
    return publish_webhook_event(
        repository=repository,
        event_type="sources/deleted",
        event_factory=lambda _webhook: {"source_id": str(source.id)},
        flow=None,
        source=source,
        flow_collected_by_ids=[],
        source_collected_by_ids=source_collected_by_ids,
        webhooks=webhooks,
    )


def segment_payload(
    segment: SegmentRecord,
    get_urls: list[JsonPayload],
    *,
    init_object: MediaObjectRecord | None = None,
    init_get_urls: list[JsonPayload] | None = None,
    include_object_timerange: bool = False,
) -> JsonPayload:
    payload = {
        "object_id": segment.object_id,
        "timerange": segment.timerange,
        "ts_offset": segment.ts_offset,
        "last_duration": segment.last_duration,
        "object_timerange": (
            segment_object_timerange(segment) if include_object_timerange else None
        ),
        "sample_offset": segment.sample_offset,
        "sample_count": segment.sample_count,
        "get_urls": get_urls,
        "key_frame_count": segment.key_frame_count,
        "init_object": (
            {
                "object_id": init_object.id,
                "get_urls": init_get_urls or [],
            }
            if init_object is not None
            else None
        ),
    }
    return without_none(payload)


def segments_added_event(
    *,
    flow: FlowRecord,
    segments: list[SegmentRecord],
    objects_by_id: dict[str, MediaObjectRecord],
    webhook_data: WebhookData,
    object_storage: ObjectStorage,
) -> JsonPayload:
    event_objects = []
    for object_id in dict.fromkeys(
        object_id
        for segment in segments
        for object_id in (
            segment.object_id,
            *([segment.init_object_id] if segment.init_object_id else []),
        )
    ):
        media_object = objects_by_id.get(object_id)
        if media_object is not None:
            event_objects.append(media_object)
    presigned_value = webhook_data.get("presigned")
    presigned = presigned_value if isinstance(presigned_value, bool) else None
    get_urls_by_object = (
        objects_get_urls(
            event_objects,
            object_storage=object_storage,
            accept_get_urls=_optional_string_set(webhook_data.get("accept_get_urls")),
            accept_storage_ids=_optional_string_set(
                webhook_data.get("accept_storage_ids")
            ),
            presigned=presigned,
            verbose_storage=bool(webhook_data.get("verbose_storage")),
        )
        if event_objects
        else {}
    )
    include_object_timerange = bool(webhook_data.get("include_object_timerange"))
    return {
        "flow_id": str(flow.id),
        "segments": [
            segment_payload(
                segment,
                get_urls_by_object.get(segment.object_id, []),
                init_object=(
                    objects_by_id.get(segment.init_object_id)
                    if segment.init_object_id is not None
                    else None
                ),
                init_get_urls=(
                    get_urls_by_object.get(segment.init_object_id, [])
                    if segment.init_object_id is not None
                    else None
                ),
                include_object_timerange=include_object_timerange,
            )
            for segment in segments
        ],
    }


def flow_event_context(
    repository: FlowEventResourceRepository,
    flow: FlowRecord,
) -> FlowEventContext:
    source = (
        repository.get_source(flow.source_id) if flow.source_id is not None else None
    )
    parents = repository.list_flows_collecting([flow.id])
    collected_by_ids = collected_by_by_flow_id(parents).get(flow.id, [])
    return FlowEventContext(
        source=source,
        flow_collected_by_ids=collected_by_ids,
        source_collected_by_ids=source_collected_by_ids(repository, source),
    )


def source_collected_by_ids(
    repository: FlowEventResourceRepository,
    source: SourceRecord | None,
) -> list[str]:
    if source is None:
        return []
    child_ids = {flow.id for flow in repository.list_flows_by_source(source.id)}
    if not child_ids:
        return []
    collected_by: list[str] = []
    for parent_flow in repository.list_flows_collecting(child_ids):
        if parent_flow.source_id is None:
            continue
        for item in flow_collection(parent_flow):
            child_flow_id = collection_child_id(item)
            if child_flow_id is None:
                continue
            if child_flow_id not in child_ids:
                continue
            source_id = str(parent_flow.source_id)
            if source_id not in collected_by:
                collected_by.append(source_id)
    return collected_by


def _optional_string_set(value: object) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()


def webhook_headers(webhook: WebhookData) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key_name = webhook.get("api_key_name")
    api_key_value = webhook.get("api_key_value")
    if api_key_name and api_key_value:
        validate_api_key_header_name(api_key_name)
        headers[str(api_key_name)] = str(api_key_value)
    return headers


def _parse_webhook_url(url: str) -> ParseResult:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("Webhook URL must use http or https")
    if not parsed.hostname:
        raise ValueError("Webhook URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Webhook URL must not include credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Webhook URL port is invalid") from exc
    if port is not None and not 0 < port <= 65535:
        raise ValueError("Webhook URL port is invalid")
    return parsed


def _webhook_host_allowed(hostname: str, allowed_hosts: tuple[str, ...]) -> bool:
    for configured in allowed_hosts:
        pattern = _normalized_hostname(configured)
        if "*" not in pattern and hostname == pattern:
            return True
        if not pattern.startswith("*."):
            continue
        suffix = pattern[2:]
        if (
            "." in suffix
            and "*" not in suffix
            and all(suffix.split("."))
            and _ip_address(suffix) is None
            and _ip_address(hostname) is None
            and "*" not in hostname
            and all(hostname.split("."))
            and hostname.endswith("." + suffix)
        ):
            return True
    return False


def validate_webhook_target(
    parsed: ParseResult,
    *,
    egress_policy: WebhookEgressPolicy | None = None,
) -> None:
    policy = egress_policy or WebhookEgressPolicy()
    hostname = _normalized_hostname(parsed.hostname or "")
    address = _ip_address(hostname)
    if (
        parsed.scheme != "https"
        or parsed.port not in (None, 443)
        or parsed.fragment
        or not _webhook_host_allowed(hostname, policy.allowed_hosts)
        or _is_kubernetes_service_name(hostname)
        or hostname in _METADATA_HOSTNAMES
        or (address is not None and _blocked_ip(address))
    ):
        logger.warning("Webhook target rejected: hostname=%r", hostname[:253])
        raise WebhookEgressError(
            "Webhook target must be an explicitly allowed public HTTPS host"
        )


def retry_delay(attempt_count: int) -> int:
    index = max(0, min(attempt_count - 1, len(_DEFAULT_RETRY_BACKOFF_SECONDS) - 1))
    return _DEFAULT_RETRY_BACKOFF_SECONDS[index]


def _list_of_strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _optional_list_of_strings(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return None


def _selector_matches(candidates: list[str], required: list[str]) -> bool:
    if not required:
        return True
    if not candidates:
        return False
    candidate_set = set(candidates)
    return any(item in candidate_set for item in required)


def _collection_selector_matches(
    candidates: list[str], required: list[str] | None
) -> bool:
    if required is None:
        return True
    if not required:
        return not candidates
    if not candidates:
        return False
    candidate_set = set(candidates)
    return any(item in candidate_set for item in required)


def _normalized_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


def _is_kubernetes_service_name(hostname: str) -> bool:
    return (
        hostname == "localhost"
        or hostname.endswith(".svc")
        or hostname.endswith(".svc.cluster.local")
        or hostname.endswith(".cluster.local")
    )


def _ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address in _CLOUD_METADATA_IPS
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
