from fastapi import Depends, FastAPI

from tamoss.api.routes import (
    delete_requests,
    flows,
    objects,
    profiles,
    segments,
    service,
    sources,
    storage,
    webhooks,
)
from tamoss.contract.openapi import load_public_openapi
from tamoss.auth import authorize_request
from tamoss.errors import register_error_handlers


def create_app(use_cases):
    app = FastAPI(
        title="Cloudflare TAMS",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(authorize_request)],
    )
    app.state.tamoss_use_cases = use_cases
    app.state.tamoss_settings = use_cases.settings
    register_error_handlers(app)
    for module in (
        service,
        profiles,
        webhooks,
        delete_requests,
        sources,
        flows,
        storage,
        segments,
        objects,
    ):
        app.include_router(module.router)
    app.openapi = lambda: load_public_openapi(use_cases.settings)

    @app.get("/openapi.json", include_in_schema=False)
    @app.head("/openapi.json", include_in_schema=False)
    async def openapi_schema():
        return app.openapi()

    return app
