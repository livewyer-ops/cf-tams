# Adapted from TAMOSS for the Cloudflare Workers runtime.
from __future__ import annotations

import json
from typing import Annotated, cast
from uuid import UUID

from fastapi import Request
from pydantic import BeforeValidator

from tamoss.application.contexts.deletion import DeletionUseCases
from tamoss.application.contexts.flows import FlowUseCases
from tamoss.application.contexts.objects import ObjectUseCases
from tamoss.application.contexts.profiles import ProfileUseCases
from tamoss.application.contexts.segments import SegmentUseCases
from tamoss.application.contexts.service import ServiceUseCases
from tamoss.application.contexts.sources import SourceUseCases
from tamoss.application.contexts.storage import StorageUseCases
from tamoss.application.contexts.webhooks import WebhookUseCases
from tamoss.application.use_cases import TamossUseCases
from tamoss.contract.validation import parse_uuid
from tamoss.errors import BadRequest, NotFound
from tamoss.settings import Settings


def _resource_uuid(value: object) -> UUID:
    try:
        return cast(UUID, parse_uuid(value))
    except ValueError:
        raise NotFound("The requested resource ID in the path is invalid.") from None


ResourceUUID = Annotated[UUID, BeforeValidator(_resource_uuid)]


async def require_json_body(request: Request) -> None:
    try:
        json.loads(await request.body())
    except UnicodeDecodeError, ValueError:
        raise BadRequest("Bad request. Body must be valid JSON.") from None


async def get_use_cases(request: Request) -> TamossUseCases:
    return request.app.state.tamoss_use_cases


async def get_app_settings(request: Request) -> Settings:
    return request.app.state.tamoss_settings


async def get_service_use_cases(request: Request) -> ServiceUseCases:
    return (await get_use_cases(request)).service


async def get_profile_use_cases(request: Request) -> ProfileUseCases:
    return (await get_use_cases(request)).profiles


async def get_webhook_use_cases(request: Request) -> WebhookUseCases:
    return (await get_use_cases(request)).webhooks


async def get_deletion_use_cases(request: Request) -> DeletionUseCases:
    return (await get_use_cases(request)).deletion


async def get_source_use_cases(request: Request) -> SourceUseCases:
    return (await get_use_cases(request)).sources


async def get_flow_use_cases(request: Request) -> FlowUseCases:
    return (await get_use_cases(request)).flows


async def get_storage_use_cases(request: Request) -> StorageUseCases:
    return (await get_use_cases(request)).storage


async def get_segment_use_cases(request: Request) -> SegmentUseCases:
    use_cases = await get_use_cases(request)
    if request.method == "POST":
        await require_json_body(request)
        await use_cases.object_storage.prepare_registration(await request.json())
    return use_cases.segments


async def get_object_use_cases(request: Request) -> ObjectUseCases:
    return (await get_use_cases(request)).objects
