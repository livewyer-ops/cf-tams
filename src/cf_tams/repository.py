# Adapted from TAMOSS repository semantics for Durable Object SQLite.
from __future__ import annotations
from collections.abc import Iterable
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from uuid import UUID
from tamoss.domain import flow_collections
from tamoss.domain import segments as segment_domain
from tamoss.domain.exceptions import SEGMENT_OVERLAP_MESSAGE, SegmentOverlapError
from tamoss.domain.listing_pagination import page_listing_sequence
from tamoss.domain.listings import (
    DeleteRequestSortBy,
    FlowSortBy,
    SourceSortBy,
    sorted_listing,
)
from tamoss.domain.model import (
    DeletionRequestRecord,
    DomainErrorPayload,
    FlowRecord,
    MediaObjectRecord,
    ObjectCleanupRecord,
    ObjectCopyRecord,
    ProfileRecord,
    SegmentRecord,
    ServiceMetadata,
    SourceRecord,
    SourceRelationships,
    StorageBackend,
    WebhookDeliveryRecord,
    WebhookRecord,
)
from tamoss.domain.pagination import Page, page_sequence
from tamoss.domain.tags import tags_match
from tamoss.errors import ConfigurationError, Forbidden, normalize_error_payload

from cf_tams.records import RecordMap
from pydantic import TypeAdapter
from mediatimestamp import Timestamp
from tamoss.domain.pagination import resolve_page_window

_SEGMENT = TypeAdapter(SegmentRecord)
_SHIFT = Timestamp.MAX_SECONDS * Timestamp.MAX_NANOSEC
_WIDTH = len(str(2 * _SHIFT))


def bound_key(value: int) -> str:
    # Decimal text preserves nanoseconds across the JavaScript FFI boundary.
    return str(value + _SHIFT).zfill(_WIDTH)


def _segment_timerange_filter(*, start, end, is_empty, is_point):
    where, params = "", []
    if is_empty:
        where += " AND 0"
    if start is not None:
        where += " AND end > ?"
        params.append(bound_key(start))
    end = segment_domain.timerange_query_end(start, end, is_point)
    if end is not None:
        where += " AND start < ?"
        params.append(bound_key(end))
    return where, params


class SQLiteRepository:
    def __init__(self, storage, storage_backends):
        self.storage = storage
        self.sql = storage.sql
        self.in_transaction = False
        self._storage_backends = storage_backends
        self._storage_backend = next(
            backend for backend in storage_backends if backend.default_storage
        )
        self.sql.exec(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
        )
        if not self.sql.exec(
            "SELECT version FROM schema_migrations WHERE version = 1"
        ).toArray():

            def migrate():
                self.sql.exec(
                    "CREATE TABLE records (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (kind, id))"
                )
                self.sql.exec(
                    "CREATE INDEX records_status ON records (kind, json_extract(payload, '$.status'))"
                )
                self.sql.exec(
                    "CREATE INDEX records_unreferenced ON records (json_extract(payload, '$.created')) WHERE kind = 'objects' AND json_array_length(json_extract(payload, '$.referenced_by_flows')) = 0"
                )
                self.sql.exec(
                    "CREATE TABLE segments (flow_id TEXT NOT NULL, object_id TEXT NOT NULL, init_object_id TEXT, start TEXT NOT NULL, end TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (flow_id, end, start, object_id))"
                )
                self.sql.exec(
                    "CREATE INDEX segments_start ON segments (flow_id, start)"
                )
                self.sql.exec(
                    "CREATE INDEX segments_object ON segments (flow_id, object_id)"
                )
                self.sql.exec(
                    "CREATE INDEX segments_init ON segments (flow_id, init_object_id)"
                )
                self.sql.exec("INSERT INTO schema_migrations VALUES (1)")

            storage.transactionSync(migrate)
        self._flows = RecordMap(self.sql, "flows", FlowRecord)
        self._profiles = RecordMap(self.sql, "profiles", ProfileRecord)
        self._sources = RecordMap(self.sql, "sources", SourceRecord)
        self._objects = RecordMap(self.sql, "objects", MediaObjectRecord, key_type=str)
        self._webhooks = RecordMap(self.sql, "webhooks", WebhookRecord)
        self._webhook_deliveries = RecordMap(
            self.sql, "webhook_deliveries", WebhookDeliveryRecord
        )
        self._delete_requests = RecordMap(
            self.sql, "delete_requests", DeletionRequestRecord
        )
        self._object_cleanups = RecordMap(
            self.sql, "object_cleanups", ObjectCleanupRecord
        )
        self._object_copies = RecordMap(self.sql, "object_copies", ObjectCopyRecord)
        self._metadata = RecordMap(self.sql, "service", ServiceMetadata, key_type=str)
        self.atomic(self._check_storage_identities)

    def _check_storage_identities(self):
        saved = RecordMap(self.sql, "storage_backends", StorageBackend)
        previous = dict(saved.items())
        if not previous:
            # Existing installations stored backend identities inside Object instances.
            rows = self.sql.exec("""
                SELECT DISTINCT json_extract(instance.value, '$.storage_backend') AS backend
                FROM records, json_each(payload, '$.instances') AS instance
                WHERE kind = 'objects' AND json_extract(instance.value, '$.controlled') = 1
                  AND json_extract(instance.value, '$.storage_backend') IS NOT NULL
            """).toArray()
            for row in rows:
                backend = saved.adapter.validate_json(row["backend"])
                current = self.get_storage_backend(backend.id)
                if current is None or (backend.bucket_name, backend.endpoint_url) != (
                    current.bucket_name,
                    current.endpoint_url,
                ):
                    raise ConfigurationError(
                        "Configured storage does not match existing media."
                    )
                previous[backend.id] = backend
        for identity, old in previous.items():
            current = self.get_storage_backend(identity)
            if current is None or (old.bucket_name, old.endpoint_url) != (
                current.bucket_name,
                current.endpoint_url,
            ):
                raise ConfigurationError(
                    "An existing storage identity cannot be removed or remapped."
                )
        # Pending cleanup may be the only remaining reference to a legacy backend.
        for row in self.sql.exec("""
            SELECT DISTINCT json_extract(payload, '$.storage_backend_id') AS id
            FROM records WHERE kind = 'object_cleanups' AND json_extract(payload, '$.status') != 'done'
        """).toArray():
            if self.get_storage_backend(UUID(row["id"])) is None:
                raise ConfigurationError(
                    "A pending cleanup refers to an unconfigured backend."
                )
        for backend in self._storage_backends:
            saved[backend.id] = backend

    @contextmanager
    def unit_of_work(self):
        if not self.in_transaction:
            raise RuntimeError(
                "Metadata mutation requires a Durable Object transaction"
            )
        yield self

    def atomic(self, operation):
        if self.in_transaction:
            return operation()

        def invoke():
            self.in_transaction = True
            try:
                return operation()
            finally:
                self.in_transaction = False

        return self.storage.transactionSync(invoke)

    def current_schema_revision(self):
        return "1"

    def get_service_metadata(self):
        return self._metadata.get("service")

    def save_service_metadata(self, metadata):
        self._metadata["service"] = metadata

    def get_objects(self, object_ids):
        return {
            key: value
            for key in set(object_ids)
            if (value := self.get_object(key)) is not None
        }

    def _segment_rows(self, where, params, suffix=""):
        rows = self.sql.exec(
            "SELECT payload FROM segments WHERE " + where + suffix, *params
        ).toArray()
        return [_SEGMENT.validate_json(row["payload"]) for row in rows]

    def _boundary_segments(self, where, params):
        return self._segment_rows(
            where, params, " ORDER BY start ASC LIMIT 1"
        ) + self._segment_rows(where, params, " ORDER BY end DESC LIMIT 1")

    def flow_timeranges(
        self,
        flow_ids,
        *,
        timerange_start=None,
        timerange_end=None,
        timerange_is_empty=False,
        timerange_is_point=False,
    ):
        where, params = _segment_timerange_filter(
            start=timerange_start,
            end=timerange_end,
            is_empty=timerange_is_empty,
            is_point=timerange_is_point,
        )
        return {
            flow_id: segment_domain.timerange_union(
                self._boundary_segments("flow_id = ?" + where, [str(flow_id), *params])
            )
            for flow_id in flow_ids
        }

    def list_segments(self, flow_id):
        return self._segment_rows(
            "flow_id = ?", [str(flow_id)], " ORDER BY end, start, object_id"
        )

    def has_segments(self, flow_id):
        return bool(
            self.sql.exec(
                "SELECT 1 FROM segments WHERE flow_id = ? LIMIT 1", str(flow_id)
            ).toArray()
        )

    def list_segments_for_objects(self, *, flow_id, object_ids):
        found = {}
        for object_id in set(object_ids):
            for column in ("object_id", "init_object_id"):
                for segment in self._segment_rows(
                    f"flow_id = ? AND {column} = ?", [str(flow_id), object_id]
                ):
                    found[(segment.object_id, segment.timerange)] = segment
        return sorted(found.values(), key=segment_domain.segment_sort_key)

    def list_segments_overlapping(self, *, flow_id, timeranges):
        bounds = list(timeranges)
        if not bounds:
            return []
        candidates = self._segment_rows(
            "flow_id = ? AND end > ? AND start < ?",
            [
                str(flow_id),
                bound_key(min(b.start for b in bounds)),
                bound_key(max(b.end for b in bounds)),
            ],
        )
        return sorted(
            (
                s
                for s in candidates
                if any(
                    segment_domain.segment_overlaps_bounds(
                        s, start=b.start, end=b.end, requested_is_point=b.is_point
                    )
                    for b in bounds
                )
            ),
            key=segment_domain.segment_sort_key,
        )

    def _delete_where(self, delete_filter):
        where, params = "flow_id = ?", [str(delete_filter.flow_id)]
        if delete_filter.timerange_is_empty:
            where += " AND 0"
        if delete_filter.object_id is not None:
            where += " AND object_id = ?"
            params.append(delete_filter.object_id)
        if delete_filter.timerange_start is not None:
            where += " AND start >= ?"
            params.append(bound_key(delete_filter.timerange_start))
        if delete_filter.timerange_end is not None:
            where += " AND end <= ?"
            params.append(bound_key(delete_filter.timerange_end))
        return where, params

    def segment_delete_timerange(self, delete_filter):
        return segment_domain.timerange_union(
            self._boundary_segments(*self._delete_where(delete_filter))
        )

    def delete_segment_batch(self, delete_filter, *, limit):
        if limit < 1:
            return []
        where, params = self._delete_where(delete_filter)
        deleted = self._segment_rows(
            where, params + [limit], " ORDER BY end, start, object_id LIMIT ?"
        )
        for segment in deleted:
            start, end = segment_domain.segment_bounds(segment)
            self.sql.exec(
                "DELETE FROM segments WHERE flow_id = ? AND end = ? AND start = ? AND object_id = ?",
                str(segment.flow_id),
                bound_key(end),
                bound_key(start),
                segment.object_id,
            )
        return deleted

    def list_segments_page(
        self,
        *,
        flow_id,
        object_id,
        timerange_start,
        timerange_end,
        timerange_is_empty,
        timerange_is_point,
        reverse_order,
        page,
        limit,
    ):
        where, params = _segment_timerange_filter(
            start=timerange_start,
            end=timerange_end,
            is_empty=timerange_is_empty,
            is_point=timerange_is_point,
        )
        where, params = "flow_id = ?" + where, [str(flow_id), *params]
        if object_id is not None:
            where += " AND object_id = ?"
            params.append(object_id)
        window = resolve_page_window(page=page, limit=limit)
        order = "DESC" if reverse_order else "ASC"
        items = self._segment_rows(
            where,
            params + [window.limit + 1, window.offset],
            f" ORDER BY end {order}, start {order}, object_id {order} LIMIT ? OFFSET ?",
        )
        return Page(
            items=items[: window.limit],
            limit=window.limit,
            next_page=str(window.offset + window.limit)
            if len(items) > window.limit
            else None,
            timerange=segment_domain.timerange_union(items[: window.limit]),
        )

    def append_segment(self, segment):
        start, end = segment_domain.segment_bounds(segment)
        self.sql.exec(
            "INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?)",
            str(segment.flow_id),
            segment.object_id,
            segment.init_object_id,
            bound_key(start),
            bound_key(end),
            _SEGMENT.dump_json(segment).decode(),
        )

    def save_registered_segments(self, *, flow, media_objects, segments):
        self._ensure_flow_not_deleting(flow.id)
        for segment in segments:
            start, end = segment_domain.segment_bounds(segment)
            if self.sql.exec(
                "SELECT 1 FROM segments WHERE flow_id = ? AND end > ? AND start < ? LIMIT 1",
                str(segment.flow_id),
                bound_key(start),
                bound_key(end),
            ).toArray():
                raise SegmentOverlapError(SEGMENT_OVERLAP_MESSAGE)
            self.append_segment(segment)
        self.save_flow(flow)
        for media_object in media_objects:
            self.save_object(media_object)

    def list_unreferenced_objects_created_before(self, *, before, limit):
        rows = self.sql.exec(
            "SELECT payload FROM records WHERE kind = 'objects' AND json_array_length(json_extract(payload, '$.referenced_by_flows')) = 0 AND json_extract(payload, '$.created') < ? ORDER BY json_extract(payload, '$.created'), id LIMIT ?",
            before.isoformat().replace("+00:00", "Z"),
            limit,
        ).toArray()
        return [self._objects.adapter.validate_json(row["payload"]) for row in rows]

    def pending(self, kind, statuses, limit=50):
        store = getattr(self, "_" + kind)
        marks = ",".join("?" for _ in statuses)
        due = ""
        if kind == "object_cleanups":
            due = " AND julianday(json_extract(payload, '$.object_created')) <= julianday('now') - 1"
        elif kind == "webhook_deliveries":
            due = " AND coalesce(julianday(json_extract(payload, '$.next_attempt_at')), 0) <= julianday('now')"
        if kind != "webhook_deliveries":
            due += " AND (json_extract(payload, '$.status') != 'error' OR julianday(json_extract(payload, '$.updated')) <= julianday('now') - 30.0/86400)"
        rows = self.sql.exec(
            f"SELECT payload FROM records WHERE kind = ? AND json_extract(payload, '$.status') IN ({marks}){due} ORDER BY json_extract(payload, '$.created'), id LIMIT ?",
            kind,
            *sorted(statuses),
            limit,
        ).toArray()
        return [store.adapter.validate_json(row["payload"]) for row in rows]

    def purge_finished_worker_records(self, *, older_than, limit):
        rows = self.sql.exec(
            "SELECT kind, id FROM records WHERE kind IN ('webhook_deliveries', 'delete_requests', 'object_cleanups', 'object_copies') AND json_extract(payload, '$.status') IN ('done', 'dead') AND json_extract(payload, '$.updated') < ? LIMIT ?",
            older_than.isoformat().replace("+00:00", "Z"),
            limit,
        ).toArray()
        for row in rows:
            self.sql.exec(
                "DELETE FROM records WHERE kind = ? AND id = ?", row["kind"], row["id"]
            )
        return len(rows)

    @property
    def service_repository(self) -> SQLiteRepository:
        return self

    @property
    def profile_repository(self) -> SQLiteRepository:
        return self

    @property
    def webhook_repository(self) -> SQLiteRepository:
        return self

    @property
    def deletion_repository(self) -> SQLiteRepository:
        return self

    @property
    def source_repository(self) -> SQLiteRepository:
        return self

    @property
    def flow_repository(self) -> SQLiteRepository:
        return self

    @property
    def storage_repository(self) -> SQLiteRepository:
        return self

    @property
    def segment_repository(self) -> SQLiteRepository:
        return self

    @property
    def object_repository(self) -> SQLiteRepository:
        return self

    def lock_flow_segments(self, flow_id: UUID) -> None:
        return None

    def lock_source(self, source_id: UUID) -> None:
        return None

    def lock_profile(self, profile_id: UUID) -> None:
        return None

    def lock_objects(self, object_ids: Iterable[str]) -> None:
        return None

    def list_profiles_page(
        self,
        *,
        format: str | None,
        codec: str | None,
        label: str | None,
        page: str | None,
        limit: int | None,
    ) -> Page[ProfileRecord]:
        profiles = list(self._profiles.values())
        if format is not None:
            profiles = [
                item for item in profiles if item.flow_metadata.get("format") == format
            ]
        if codec is not None:
            profiles = [
                item for item in profiles if item.flow_metadata.get("codec") == codec
            ]
        if label is not None:
            profiles = [item for item in profiles if item.label == label]
        profiles.sort(key=lambda item: str(item.id))
        return page_sequence(profiles, page=page, limit=limit)

    def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        profile = self._profiles.get(profile_id)
        return deepcopy(profile) if profile is not None else None

    def create_profile(self, profile: ProfileRecord) -> bool:
        if profile.id in self._profiles:
            return False
        self._profiles[profile.id] = deepcopy(profile)
        return True

    def count_flows_by_profile(self, profile_id: UUID) -> int:
        return sum(
            (1 for flow in self._flows.values() if flow.profile_id == profile_id)
        )

    def delete_profile(self, profile_id: UUID) -> bool:
        return self._profiles.pop(profile_id, None) is not None

    def list_storage_backends(self) -> list[StorageBackend]:
        return list(self._storage_backends)

    def list_storage_backends_page(
        self,
        *,
        tag_values: dict[str, set[str]],
        tag_exists: dict[str, bool],
        reverse_order: bool,
        page: str | None,
        limit: int | None,
    ) -> Page[StorageBackend]:
        backends = [
            backend
            for backend in self._storage_backends
            if tags_match(backend.tags, tag_values, tag_exists)
        ]
        backends = sorted_listing(
            backends,
            value=lambda backend: backend.label,
            identity=lambda backend: str(backend.id),
            descending=reverse_order,
            missing_first=reverse_order,
        )
        return page_sequence(backends, page=page, limit=limit)

    def default_storage_backend(self) -> StorageBackend | None:
        return self._storage_backend

    def get_storage_backend(self, storage_id: UUID) -> StorageBackend | None:
        for backend in self._storage_backends:
            if storage_id == backend.id:
                return backend
        return None

    def list_flows_by_source(self, source_id: UUID) -> list[FlowRecord]:
        return [flow for flow in self._flows.values() if flow.source_id == source_id]

    def list_flows_collecting(self, flow_ids: Iterable[UUID]) -> list[FlowRecord]:
        requested_ids = set(flow_ids)
        flows = list(self._flows.values())
        return [
            flow
            for flow in flows
            if any(
                (
                    flow_collections.collection_child_id(item) in requested_ids
                    for item in flow_collections.flow_collection(flow)
                )
            )
        ]

    def list_flows_page(
        self,
        *,
        source_id: UUID | None,
        timerange_start: int | None,
        timerange_end: int | None,
        timerange_is_empty: bool,
        timerange_is_point: bool,
        format: str | None,
        profile_id: UUID | None,
        status: str | None,
        init_segments: bool | None,
        collected_by_ids: set[UUID] | None,
        top_level_only: bool,
        sort_by: FlowSortBy,
        reverse_order: bool,
        codec: str | None,
        label: str | None,
        frame_width: int | None,
        frame_height: int | None,
        tag_values: dict[str, set[str]],
        tag_exists: dict[str, bool],
        page: str | None,
        limit: int | None,
    ) -> Page[FlowRecord]:
        flows = list(self._flows.values())
        segments = (
            {
                flow.id: self._boundary_segments("flow_id = ?", [str(flow.id)])
                for flow in flows
            }
            if timerange_is_empty
            or timerange_start is not None
            or timerange_end is not None
            else {}
        )
        collected_by = flow_collections.collected_by_by_flow_id(flows)
        flows = [
            flow_collections.flow_with_collected_by(flow, collected_by.get(flow.id, []))
            for flow in flows
        ]
        if source_id is not None:
            flows = [flow for flow in flows if flow.source_id == source_id]
        if format is not None:
            flows = [flow for flow in flows if flow.format == format]
        if profile_id is not None:
            flows = [flow for flow in flows if flow.profile_id == profile_id]
        if status is not None:
            flows = [flow for flow in flows if flow.status == status]
        if init_segments is not None:
            flows = [flow for flow in flows if flow.init_segments == init_segments]
        if top_level_only:
            flows = [flow for flow in flows if not collected_by.get(flow.id)]
        elif collected_by_ids is not None:
            flows = [
                flow
                for flow in flows
                if collected_by_ids.intersection(
                    (UUID(parent_id) for parent_id in collected_by.get(flow.id, []))
                )
            ]
        if codec is not None:
            flows = [
                flow
                for flow in flows
                if flow_collections.flow_data_value(flow, "codec") == codec
            ]
        if label is not None:
            flows = [flow for flow in flows if flow.data.get("label") == label]
        if frame_width is not None:
            flows = [
                flow
                for flow in flows
                if flow_collections.flow_data_value(flow, "frame_width") == frame_width
            ]
        if frame_height is not None:
            flows = [
                flow
                for flow in flows
                if flow_collections.flow_data_value(flow, "frame_height")
                == frame_height
            ]
        if timerange_is_empty:
            flows = [flow for flow in flows if not segments.get(flow.id)]
        elif timerange_start is not None or timerange_end is not None:
            flows = [
                flow
                for flow in flows
                if segment_domain.flow_timerange_matches(
                    segments.get(flow.id, []),
                    start=timerange_start,
                    end=timerange_end,
                    requested_is_point=timerange_is_point,
                )
            ]
        flows = [
            flow for flow in flows if tags_match(flow.tags, tag_values, tag_exists)
        ]
        flow_sort_value = {
            FlowSortBy.CREATED: lambda flow: flow.created,
            FlowSortBy.METADATA_UPDATED: lambda flow: flow.metadata_updated,
            FlowSortBy.LABEL: lambda flow: flow.data.get("label"),
        }[sort_by]
        flows = sorted_listing(
            flows,
            value=flow_sort_value,
            identity=lambda flow: str(flow.id),
            descending=sort_by.descending(reverse_order=reverse_order),
            missing_first=reverse_order,
        )
        return page_listing_sequence(
            flows,
            page=page,
            limit=limit,
            resource="flows",
            sort_by=sort_by,
            reverse_order=reverse_order,
            value=flow_sort_value,
            identity=lambda flow: flow.id,
        )

    def get_flow(self, flow_id: UUID) -> FlowRecord | None:
        return self._flows.get(flow_id)

    def save_flow(self, flow: FlowRecord) -> None:
        self._flows[flow.id] = flow

    def delete_flow(self, flow_id: UUID) -> None:
        self._flows.pop(flow_id, None)

    def get_source(self, source_id: UUID) -> SourceRecord | None:
        return self._sources.get(source_id)

    def save_source(self, source: SourceRecord) -> None:
        self._sources[source.id] = source

    def delete_source(self, source_id: UUID) -> None:
        self._sources.pop(source_id, None)

    def list_sources_page(
        self,
        *,
        label: str | None,
        format: str | None,
        collected_by_ids: set[UUID] | None,
        top_level_only: bool,
        sort_by: SourceSortBy,
        reverse_order: bool,
        tag_values: dict[str, set[str]],
        tag_exists: dict[str, bool],
        page: str | None,
        limit: int | None,
    ) -> Page[SourceRecord]:
        sources = list(self._sources.values())
        if label is not None:
            sources = [source for source in sources if source.label == label]
        if format is not None:
            sources = [source for source in sources if source.format == format]
        relationships = self.source_relationships_for((source.id for source in sources))
        if top_level_only:
            sources = [
                source
                for source in sources
                if not relationships[source.id].collected_by
            ]
        elif collected_by_ids is not None:
            sources = [
                source
                for source in sources
                if collected_by_ids.intersection(relationships[source.id].collected_by)
            ]
        sources = [
            source
            for source in sources
            if tags_match(source.tags, tag_values, tag_exists)
        ]
        source_sort_value = {
            SourceSortBy.CREATED: lambda source: source.created,
            SourceSortBy.UPDATED: lambda source: source.metadata_updated,
            SourceSortBy.LABEL: lambda source: source.label,
        }[sort_by]
        sources = sorted_listing(
            sources,
            value=source_sort_value,
            identity=lambda source: str(source.id),
            descending=sort_by.descending(reverse_order=reverse_order),
            missing_first=reverse_order,
        )
        return page_listing_sequence(
            sources,
            page=page,
            limit=limit,
            resource="sources",
            sort_by=sort_by,
            reverse_order=reverse_order,
            value=source_sort_value,
            identity=lambda source: source.id,
        )

    def source_relationships_for(
        self, source_ids: Iterable[UUID]
    ) -> dict[UUID, SourceRelationships]:
        requested_ids = list(source_ids)
        relationships = {
            source_id: SourceRelationships(source_collection=[], collected_by=[])
            for source_id in requested_ids
        }
        flows = list(self._flows.values())
        flows_by_id = {flow.id: flow for flow in flows}
        for parent_flow in flows:
            if parent_flow.source_id is None:
                continue
            for item in flow_collections.flow_collection(parent_flow):
                child_flow_id = flow_collections.collection_child_id(item)
                if child_flow_id is None:
                    continue
                child_flow = flows_by_id.get(child_flow_id)
                if child_flow is None or child_flow.source_id is None:
                    continue
                role = item.get("role")
                if parent_flow.source_id in relationships:
                    source_item = {"id": str(child_flow.source_id)}
                    if isinstance(role, str):
                        source_item["role"] = role
                    source_collection = relationships[
                        parent_flow.source_id
                    ].source_collection
                    if source_item not in source_collection:
                        source_collection.append(source_item)
                if child_flow.source_id in relationships:
                    collected_by = relationships[child_flow.source_id].collected_by
                    if parent_flow.source_id not in collected_by:
                        collected_by.append(parent_flow.source_id)
        return relationships

    def get_object(self, object_id: str) -> MediaObjectRecord | None:
        record = self._objects.get(object_id)
        if record:
            for instance in record.instances:
                if instance.controlled and instance.storage_backend:
                    backend = self.get_storage_backend(instance.storage_backend.id)
                    if backend is None:
                        raise ConfigurationError(
                            "Object refers to an unconfigured backend."
                        )
                    instance.storage_backend, instance.label = backend, backend.label
        return record

    def save_object_copy(self, copy: ObjectCopyRecord) -> None:
        self._object_copies[copy.id] = copy

    def has_pending_copy(self, record, destination_id):
        return bool(
            self.sql.exec(
                """
            SELECT 1 FROM records WHERE kind = 'object_copies'
              AND json_extract(payload, '$.object_id') = ?
              AND json_extract(payload, '$.storage_key') = ?
              AND json_extract(payload, '$.destination_storage_backend_id') = ?
              AND json_extract(payload, '$.status') != 'done' LIMIT 1
        """,
                record.id,
                record.storage_key,
                str(destination_id),
            ).toArray()
        )

    def save_object(self, media_object: MediaObjectRecord) -> None:
        self._objects[media_object.id] = media_object

    def create_object(self, media_object: MediaObjectRecord) -> bool:
        self._ensure_flow_not_deleting(media_object.allocated_by_flow)
        if media_object.id in self._objects:
            return False
        self._objects[media_object.id] = media_object
        return True

    def create_objects(self, media_objects: Iterable[MediaObjectRecord]) -> set[str]:
        created: set[str] = set()
        checked = set()
        for media_object in media_objects:
            if media_object.allocated_by_flow not in checked:
                self._ensure_flow_not_deleting(media_object.allocated_by_flow)
                checked.add(media_object.allocated_by_flow)
            if media_object.id in self._objects:
                continue
            self._objects[media_object.id] = media_object
            created.add(media_object.id)
        return created

    def _ensure_flow_not_deleting(self, flow_id):
        if (
            flow_id is not None
            and self.sql.exec(
                "SELECT 1 FROM records WHERE kind = 'delete_requests' AND json_extract(payload, '$.flow_id') = ? AND json_extract(payload, '$.delete_flow') = 1 AND json_extract(payload, '$.status') != 'done' LIMIT 1",
                str(flow_id),
            ).toArray()
        ):
            raise Forbidden("The Flow is being deleted.")

    def delete_object(self, object_id: str) -> None:
        self._objects.pop(object_id, None)

    def list_webhooks(self) -> list[WebhookRecord]:
        return list(self._webhooks.values())

    def list_webhooks_page(
        self,
        *,
        tag_values: dict[str, set[str]],
        tag_exists: dict[str, bool],
        reverse_order: bool,
        page: str | None,
        limit: int | None,
    ) -> Page[WebhookRecord]:
        webhooks = list(self._webhooks.values())
        webhooks = [
            webhook
            for webhook in webhooks
            if tags_match(webhook.tags, tag_values, tag_exists)
        ]
        webhooks.sort(key=lambda webhook: str(webhook.id), reverse=reverse_order)
        webhooks.sort(
            key=lambda webhook: str(webhook.data["url"]), reverse=reverse_order
        )
        return page_sequence(webhooks, page=page, limit=limit)

    def list_flow_ids_matching_tags_page(
        self,
        *,
        flow_ids: Iterable[UUID],
        tag_values: dict[str, set[str]],
        tag_exists: dict[str, bool],
        page: str | None,
        limit: int | None,
    ) -> Page[UUID]:
        requested_ids = set(flow_ids)
        flows = [
            flow for flow_id, flow in self._flows.items() if flow_id in requested_ids
        ]
        matched_ids = [
            flow.id for flow in flows if tags_match(flow.tags, tag_values, tag_exists)
        ]
        matched_ids.sort(key=str)
        return page_sequence(matched_ids, page=page, limit=limit)

    def get_webhook(self, webhook_id: UUID) -> WebhookRecord | None:
        return self._webhooks.get(webhook_id)

    def save_webhook(self, webhook: WebhookRecord) -> None:
        _normalize_webhook_error(webhook)
        self._webhooks[webhook.id] = webhook

    def delete_webhook(self, webhook_id: UUID) -> None:
        self._webhooks.pop(webhook_id, None)

    def list_webhook_deliveries(self) -> list[WebhookDeliveryRecord]:
        return deepcopy(list(self._webhook_deliveries.values()))

    def get_webhook_delivery(self, delivery_id: UUID) -> WebhookDeliveryRecord | None:
        return deepcopy(self._webhook_deliveries.get(delivery_id))

    def save_webhook_delivery(self, delivery: WebhookDeliveryRecord) -> None:
        delivery.error = DomainErrorPayload.from_json_dict(delivery.error)
        self._webhook_deliveries[delivery.id] = delivery

    def list_delete_requests_page(
        self,
        *,
        sort_by: DeleteRequestSortBy,
        reverse_order: bool,
        retention_seconds: int,
        page: str | None,
        limit: int | None,
    ) -> Page[DeletionRequestRecord]:
        requests = deepcopy(list(self._delete_requests.values()))
        value = {
            DeleteRequestSortBy.CREATED: lambda request: request.created,
            DeleteRequestSortBy.EXPIRY: lambda request: (
                request.updated + timedelta(seconds=retention_seconds)
                if request.status == "done" and retention_seconds > 0
                else None
            ),
        }[sort_by]
        requests = sorted_listing(
            requests,
            value=value,
            identity=lambda request: str(request.id),
            descending=sort_by.descending(reverse_order=reverse_order),
            missing_first=reverse_order,
        )
        return page_sequence(requests, page=page, limit=limit)

    def get_delete_request(self, request_id: UUID) -> DeletionRequestRecord | None:
        return deepcopy(self._delete_requests.get(request_id))

    def save_delete_request(self, request: DeletionRequestRecord) -> None:
        request.error = DomainErrorPayload.from_json_dict(request.error)
        self._delete_requests[request.id] = request

    def list_object_cleanups(
        self, *, delete_request_id: UUID | None = None, statuses: set[str] | None = None
    ) -> list[ObjectCleanupRecord]:
        cleanups = deepcopy(list(self._object_cleanups.values()))
        if delete_request_id is not None:
            cleanups = [
                cleanup
                for cleanup in cleanups
                if cleanup.delete_request_id == delete_request_id
            ]
        if statuses is not None:
            cleanups = [cleanup for cleanup in cleanups if cleanup.status in statuses]
        cleanups.sort(key=lambda cleanup: (cleanup.created, str(cleanup.id)))
        return cleanups

    def save_object_cleanup(self, cleanup: ObjectCleanupRecord) -> None:
        cleanup.error = DomainErrorPayload.from_json_dict(cleanup.error)
        self._object_cleanups[cleanup.id] = cleanup


def _normalize_webhook_error(webhook: WebhookRecord) -> None:
    if "error" not in webhook.data:
        return
    error = normalize_error_payload(webhook.data.get("error"))
    if error is None:
        webhook.data.pop("error", None)
    else:
        webhook.data["error"] = error
