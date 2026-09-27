# Adapted from TAMOSS: delivery is performed by Durable Object alarms.
from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4


from tamoss.application import webhooks as webhooking
from tamoss.domain.model import (
    DomainErrorPayload,
    WebhookDeliveryRecord,
    WebhookRecord,
    utc_now,
)
from tamoss.domain.pagination import Page
from tamoss.errors import BadRequest, NotFound
from tamoss.ports.repositories import WebhookRepository
from tamoss.settings import Settings


class WebhookUseCases:
    repository: WebhookRepository
    settings: Settings

    def __init__(
        self,
        *,
        repository: WebhookRepository,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.settings = settings

    def list_webhooks(
        self,
        *,
        tag_values: dict[str, set[str]],
        tag_exists: dict[str, bool],
        reverse_order: bool,
        page: str | None,
        limit: int | None,
    ) -> Page[WebhookRecord]:
        return self.repository.list_webhooks_page(
            tag_values=tag_values,
            tag_exists=tag_exists,
            reverse_order=reverse_order,
            page=page,
            limit=limit,
        )

    def get_webhook(self, webhook_id: UUID) -> WebhookRecord:
        webhook = self.repository.get_webhook(webhook_id)
        if webhook is None:
            raise NotFound("The requested Webhook ID in the path is invalid.")
        return webhook

    def create_webhook(self, webhook: Mapping[str, Any]) -> WebhookRecord:
        data = dict(webhook)
        try:
            webhooking.validate_webhook_configuration(
                data,
                egress_policy=self._webhook_egress_policy(),
            )
        except ValueError as exc:
            raise BadRequest("Bad request. Invalid webhook payload.") from exc
        status = data.pop("status", None) or "created"
        data.pop("tags", None)
        data.pop("error", None)
        webhook = WebhookRecord(
            id=uuid4(),
            data=data,
            status=status,
            tags=dict(webhook.get("tags") or {}),
        )
        self.repository.save_webhook(webhook)
        return webhook

    def put_webhook(
        self, *, webhook_id: UUID, webhook: Mapping[str, Any]
    ) -> WebhookRecord:
        if UUID(str(webhook.get("id"))) != webhook_id:
            raise NotFound("The requested Webhook ID in the path is invalid.")
        existing = self.get_webhook(webhook_id)
        if existing.status == "error" and webhook.get("status") == "disabled":
            raise BadRequest(
                "Bad request. The Webhook is currently in an error status and "
                "therefore cannot be updated to disabled."
            )

        data = dict(webhook)
        try:
            webhooking.validate_webhook_configuration(
                data,
                egress_policy=self._webhook_egress_policy(),
            )
        except ValueError as exc:
            raise BadRequest("Bad request. Invalid webhook payload.") from exc
        data.pop("id", None)
        status = data.pop("status")
        data.pop("tags", None)
        data.pop("error", None)
        webhook = WebhookRecord(
            id=webhook_id,
            data=data,
            status=status,
            tags=dict(webhook.get("tags") or {}),
        )
        self.repository.save_webhook(webhook)
        return webhook

    def delete_webhook(self, webhook_id: UUID) -> None:
        self.get_webhook(webhook_id)
        self.repository.delete_webhook(webhook_id)

    def _webhook_egress_policy(self) -> webhooking.WebhookEgressPolicy:
        return webhooking.WebhookEgressPolicy(
            allow_private_targets=self.settings.webhook_allow_private_targets,
            allowed_hosts=tuple(self.settings.webhook_allowed_hosts),
        )

    def _mark_webhook_delivery_retry(
        self,
        delivery: WebhookDeliveryRecord,
        *,
        response_status: int | None,
        error_type: str,
        error_summary: str,
    ) -> WebhookDeliveryRecord:
        delivery.status = "pending"
        delivery.response_status = response_status
        delivery.error = DomainErrorPayload.create(error_type, error_summary)
        delivery.next_attempt_at = utc_now() + timedelta(
            seconds=webhooking.retry_delay(delivery.attempt_count)
        )
        _clear_webhook_delivery_claim(delivery)
        delivery.updated = utc_now()
        self.repository.save_webhook_delivery(delivery)
        return delivery

    def _mark_webhook_delivery_dead(
        self,
        delivery: WebhookDeliveryRecord,
        *,
        response_status: int | None,
        error_type: str,
        error_summary: str,
        mark_webhook_error: bool = True,
    ) -> WebhookDeliveryRecord:
        domain_error = DomainErrorPayload.create(error_type, error_summary)
        delivery.status = "dead"
        delivery.response_status = response_status
        delivery.error = domain_error
        delivery.next_attempt_at = None
        _clear_webhook_delivery_claim(delivery)
        delivery.updated = utc_now()
        self.repository.save_webhook_delivery(delivery)
        if mark_webhook_error:
            webhook = self.repository.get_webhook(delivery.webhook_id)
            if webhook is not None:
                webhook.status = "error"
                webhook.data["error"] = domain_error.to_json_dict()
                self.repository.save_webhook(webhook)
        return delivery


def _clear_webhook_delivery_claim(delivery: WebhookDeliveryRecord) -> None:
    delivery.claimed_at = None
    delivery.claimed_by = None
    delivery.claim_expires_at = None
