"""Durable alarm work for R2 copies, deletes and webhook deliveries."""

import json
import logging
import re
from datetime import timedelta
from uuid import uuid4

from tamoss.application import webhooks
from tamoss.application.contexts import deletion_processor
from tamoss.domain.model import (
    DomainErrorPayload,
    ObjectCleanupRecord,
    ObjectInstance,
    utc_now,
)

logger = logging.getLogger(__name__)
RETENTION_SECONDS = 86400


def next_alarm(repository):
    """The earliest work deadline; an idle store has no recurring alarm."""
    rows = repository.sql.exec("""
        SELECT min(due) AS due FROM (
          SELECT CASE WHEN json_extract(payload, '$.status') = 'error'
                      THEN julianday(json_extract(payload, '$.updated')) + 30.0/86400
                      ELSE julianday('now') END AS due
          FROM records WHERE kind IN ('delete_requests', 'object_copies')
            AND json_extract(payload, '$.status') != 'done'
          UNION ALL
          SELECT max(julianday(json_extract(payload, '$.object_created')) + 1,
                     CASE WHEN json_extract(payload, '$.status') = 'error'
                          THEN julianday(json_extract(payload, '$.updated')) + 30.0/86400
                          ELSE julianday('now') END)
          FROM records WHERE kind = 'object_cleanups' AND json_extract(payload, '$.status') != 'done'
          UNION ALL
          SELECT coalesce(julianday(json_extract(payload, '$.next_attempt_at')), julianday('now'))
          FROM records WHERE kind = 'webhook_deliveries' AND json_extract(payload, '$.status') IN ('pending', 'started')
          UNION ALL
          SELECT julianday(json_extract(payload, '$.created')) + 1
          FROM records WHERE kind = 'objects' AND json_array_length(json_extract(payload, '$.referenced_by_flows')) = 0
          UNION ALL
          SELECT julianday(json_extract(payload, '$.updated')) + 1
          FROM records WHERE kind IN ('delete_requests', 'object_cleanups', 'object_copies', 'webhook_deliveries')
            AND json_extract(payload, '$.status') IN ('done', 'dead')
        )
    """).toArray()
    due = rows[0]["due"]
    return (
        max(int((due - 2440587.5) * 86400000), int(utc_now().timestamp() * 1000) + 1000)
        if due is not None
        else None
    )


async def schedule(storage, repository, *, replace=False):
    due = next_alarm(repository)
    current = await storage.getAlarm()
    if due is None:
        if current is not None:
            await storage.deleteAlarm()
    elif replace or current is None or due < current:
        await storage.setAlarm(due)


async def run_jobs(use_cases, send):
    repo = use_cases.repository
    now = utc_now()
    repo.atomic(
        lambda: use_cases.deletion.queue_stale_allocated_object_cleanups(max_objects=50)
    )
    for request in repo.pending(
        "delete_requests", {"created", "started", "error"}, limit=10
    ):
        if request.status == "error" and (now - request.updated).total_seconds() < 30:
            continue
        try:
            repo.atomic(
                lambda: deletion_processor._apply_delete_metadata(
                    repository=repo,
                    webhook_repository=repo,
                    request_id=request.id,
                )
            )
        except Exception:
            logger.exception("Deletion metadata processing failed")
            repo.atomic(
                lambda: deletion_processor._mark_delete_request_error(
                    repo,
                    request.id,
                    error_type="delete_request_failed",
                    summary="Deletion will be retried.",
                )
            )

    for job in repo.pending("object_copies", {"pending", "started", "error"}, limit=2):
        if job.object_created is None or job.storage_key is None:
            raise RuntimeError("Copy is missing its allocation identity")
        record = repo.get_object(job.object_id)
        same_allocation = (
            record is not None
            and record.created == job.object_created
            and record.storage_key == job.storage_key
        )
        backend_ids = (
            {
                instance.storage_backend.id
                for instance in record.instances
                if instance.controlled and instance.storage_backend is not None
            }
            if same_allocation
            else set()
        )
        if same_allocation and job.destination_storage_backend_id in backend_ids:
            job.status, job.error, job.updated = "done", None, utc_now()
            repo.atomic(lambda: repo.save_object_copy(job))
            continue
        if (
            not same_allocation
            or not record.referenced_by_flows
            or job.source_storage_backend_id not in backend_ids
        ):

            def abandon_copy():
                # A previous attempt may have written bytes before its metadata commit.
                repo.save_object_cleanup(
                    ObjectCleanupRecord(
                        id=uuid4(),
                        object_id=job.object_id,
                        storage_backend_id=job.destination_storage_backend_id,
                        status="pending",
                        object_created=job.object_created,
                        storage_key=job.storage_key,
                    )
                )
                job.status, job.error, job.updated = "done", None, utc_now()
                repo.save_object_copy(job)

            repo.atomic(abandon_copy)
            continue
        job.status, job.updated = "started", utc_now()
        job.attempt_count += 1
        repo.atomic(lambda: repo.save_object_copy(job))
        try:
            await use_cases.object_storage.copy(job)

            def publish_copy():
                backend = repo.get_storage_backend(job.destination_storage_backend_id)
                record.instances.append(
                    ObjectInstance(backend, None, backend.label, True)
                )
                repo.save_object(record)
                job.status, job.error, job.updated = "done", None, utc_now()
                repo.save_object_copy(job)

            repo.atomic(publish_copy)
        except Exception as exc:
            detail = re.sub(r"https?://\S+", "[R2 URL]", str(exc))
            for secret in (
                use_cases.object_storage.access_key,
                use_cases.object_storage.secret_key,
            ):
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            logger.error("R2 copy failed (%s): %s", type(exc).__name__, detail[:1000])
            job.status, job.updated = "error", utc_now()
            job.error = DomainErrorPayload.create(
                "object_copy_failed", "R2 copy will be retried."
            )
            repo.atomic(lambda: repo.save_object_copy(job))

    for cleanup in repo.pending(
        "object_cleanups", {"pending", "started", "error"}, limit=50
    ):
        # Retain bytes until upload URLs expire and the minimum Object lifetime passes.
        if cleanup.object_created is None or cleanup.storage_key is None:
            raise RuntimeError("Cleanup is missing its allocation identity")
        if (now - cleanup.object_created).total_seconds() < RETENTION_SECONDS:
            continue
        if cleanup.status == "error" and (now - cleanup.updated).total_seconds() < 30:
            continue
        try:
            record = repo.get_object(cleanup.object_id)
            restored = (
                record is not None
                and record.storage_key == cleanup.storage_key
                and any(
                    instance.controlled
                    and instance.storage_backend is not None
                    and instance.storage_backend.id == cleanup.storage_backend_id
                    for instance in record.instances
                )
            )
            if not restored:
                await use_cases.object_storage.bucket_for(
                    cleanup.storage_backend_id
                ).delete(use_cases.object_storage.key(cleanup.storage_key))
            cleanup.status, cleanup.error = "done", None
        except Exception:
            logger.exception("R2 cleanup failed")
            cleanup.status = "error"
            cleanup.error = DomainErrorPayload.create(
                "object_cleanup_failed", "R2 deletion will be retried."
            )
        cleanup.attempt_count += 1
        cleanup.updated = utc_now()
        repo.atomic(lambda: repo.save_object_cleanup(cleanup))

    for delivery in repo.pending(
        "webhook_deliveries", {"pending", "started"}, limit=10
    ):
        if (
            delivery.next_attempt_at is not None
            and delivery.next_attempt_at > utc_now()
        ):
            continue
        live = repo.get_webhook(delivery.webhook_id)
        if live is None or live.status not in {"created", "started"}:
            repo.atomic(
                lambda: use_cases.webhooks._mark_webhook_delivery_dead(
                    delivery,
                    response_status=None,
                    error_type="WebhookDisabled",
                    error_summary="Webhook is no longer active.",
                    mark_webhook_error=False,
                )
            )
            continue
        target = webhooks.webhook_for_delivery(delivery.webhook_snapshot, live.data)
        delivery.status = "started"
        delivery.attempt_count += 1
        delivery.updated = utc_now()
        repo.atomic(lambda: repo.save_webhook_delivery(delivery))
        response_status = None
        retry = False
        try:
            webhooks.validate_webhook_url(
                target.get("url"),
                egress_policy=use_cases.webhooks._webhook_egress_policy(),
            )
            webhooks.validate_api_key_header_name(target.get("api_key_name"))
            response_status = await send(
                str(target["url"]),
                webhooks.webhook_headers(target),
                json.dumps(delivery.payload),
            )
            if 200 <= response_status < 300:
                delivery.status, delivery.error, delivery.next_attempt_at = (
                    "done",
                    None,
                    None,
                )
                delivery.response_status, delivery.updated = response_status, utc_now()
                repo.atomic(lambda: repo.save_webhook_delivery(delivery))
                continue
            retry = response_status in webhooks.RETRIABLE_STATUS_CODES
            error = f"Webhook returned HTTP {response_status}."
        except ValueError:
            error = "Webhook target is no longer allowed."
        except Exception:
            retry, error = True, "Webhook delivery failed."
        transition = (
            use_cases.webhooks._mark_webhook_delivery_retry
            if retry
            and delivery.attempt_count < use_cases.settings.webhook_max_attempts
            else use_cases.webhooks._mark_webhook_delivery_dead
        )
        repo.atomic(
            lambda: transition(
                delivery,
                response_status=response_status,
                error_type="WebhookDeliveryFailed",
                error_summary=error,
            )
        )

    repo.atomic(
        lambda: repo.purge_finished_worker_records(
            older_than=utc_now() - timedelta(seconds=RETENTION_SECONDS), limit=100
        )
    )
