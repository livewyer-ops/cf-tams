import asyncio
import json
import logging
from urllib.parse import urlsplit

from js import AbortSignal, Response as WebResponse
from workers import DurableObject, Request, Response, WorkerEntrypoint, asgi, fetch

from cf_tams.app import create_app
from cf_tams.cors import load_policy, preflight, response_headers
from cf_tams.jobs import run_jobs, schedule
from cf_tams.repository import SQLiteRepository
from cf_tams.storage import R2Storage, load_backends
from tamoss.application.use_cases import TamossUseCases
from tamoss.auth import Identity
from tamoss.domain.model import utc_now
from tamoss.errors import error_payload
from tamoss.settings import Settings

MAX_BODY_BYTES = 4 * 1024 * 1024
logger = logging.getLogger(__name__)


def error(status, kind, message, **headers):
    return Response(
        json.dumps(error_payload(kind, message)),
        status=status,
        headers={"Content-Type": "application/json", **headers},
    )


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        try:
            policy = load_policy(getattr(self.env, "API_CORS", "{}"))
        except ValueError:
            return error(503, "configuration_error", "API_CORS is invalid.")
        origin = request.headers.get("Origin")
        if (
            policy.origins
            and origin
            and request.method == "OPTIONS"
            and request.headers.get("Access-Control-Request-Method")
        ):
            status, headers = preflight(
                policy,
                origin,
                request.headers.get("Access-Control-Request-Method"),
                request.headers.get("Access-Control-Request-Headers", ""),
            )
            return Response(
                "OK" if status == 200 else "Disallowed CORS request",
                status=status,
                headers=headers,
            )
        response = await self.authenticated_fetch(request)
        native = response.js_object if isinstance(response, Response) else response
        cors_headers = response_headers(
            policy, origin, native.headers.get("Vary") or ""
        )
        if not cors_headers:
            return response
        response = WebResponse.new(native.body, native)
        for key, value in cors_headers.items():
            response.headers.set(key, value)
        return response

    async def authenticated_fetch(self, request):
        if urlsplit(request.url).path == "/healthz" and request.method in {
            "GET",
            "HEAD",
        }:
            return Response("ok" if request.method == "GET" else "")
        scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
        identity = None
        if scheme.lower() == "bearer" and token and len(token) <= 16384:
            try:
                async with asyncio.timeout(10):
                    identity = await self.env.AUTH.verify(token)
            except Exception:
                logger.error("Token verification unavailable")
                return error(
                    503,
                    "authentication_unavailable",
                    "Token verification is unavailable.",
                )
        if not identity:
            return error(
                401,
                "unauthorized",
                "A valid Bearer token is required.",
                **{"WWW-Authenticate": "Bearer"},
            )
        try:
            return await self.env.STORE.getByName(
                self.env.STORE_ID
            ).fetch_authenticated(request, identity)
        except Exception:
            logger.exception("TAMS store request failed")
            return error(
                500,
                "internal_server_error",
                "The store could not complete the request.",
            )


class TamsStore(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        # One serial request per installation; partition stores if metadata throughput demands it.
        self.lock = asyncio.Lock()
        settings = Settings(webhook_allowed_hosts=json.loads(env.WEBHOOK_ALLOWED_HOSTS))
        self.repository = SQLiteRepository(
            ctx.storage, list(load_backends(env).values())
        )
        self.use_cases = TamossUseCases(
            repository=self.repository,
            object_storage=R2Storage(env, self.repository),
            settings=settings,
        )
        self.app = create_app(self.use_cases)

    async def fetch(self, request):
        return error(401, "unauthorized", "A verified identity is required.")

    async def fetch_authenticated(self, request, verified_identity):
        claims = json.loads(verified_identity)
        identity = Identity(
            subject=claims["subject"], scopes=frozenset(claims["scopes"])
        )
        # Bound the read before ASGI buffers the body, including chunked requests.
        if request.body:
            reader = request.body.getReader()
            body = bytearray()
            try:
                async with asyncio.timeout(30):
                    while True:
                        chunk = await reader.read()
                        if chunk.done:
                            break
                        if len(body) + chunk.value.byteLength > MAX_BODY_BYTES:
                            await reader.cancel()
                            return error(
                                413, "payload_too_large", "Request body exceeds 4 MiB."
                            )
                        body.extend(chunk.value.to_bytes())
            except TimeoutError:
                await reader.cancel()
                return error(408, "request_timeout", "Request body deadline exceeded.")
            finally:
                reader.releaseLock()
            request = Request(
                request.url,
                method=request.method,
                headers=dict(request.headers.items()),
                body=bytes(body),
            )
        async with self.lock:
            if urlsplit(request.url).path == "/readyz":
                if not identity.scopes.intersection(
                    {
                        "tams-api/read",
                        "tams-api/write",
                        "tams-api/delete",
                        "tams-api/admin",
                    }
                ):
                    return error(
                        403,
                        "forbidden",
                        "The token does not grant access to this operation.",
                    )
                self.repository.sql.exec("SELECT 1")
                return Response("ok")

            async def authenticated_app(scope, receive, send):
                if scope["type"] == "http":
                    scope.setdefault("state", {})["tamoss_identity"] = identity
                await self.app(scope, receive, send)

            async def operation(txn):
                self.repository.in_transaction = True
                try:
                    response = await asgi.fetch(
                        authenticated_app, request, self.env, self.ctx
                    )
                    if response.status >= 400:
                        txn.rollback()
                    else:
                        await schedule(self.ctx.storage, self.repository)
                    return response
                finally:
                    self.repository.in_transaction = False
                    self.use_cases.object_storage.metadata.clear()

            return await self.ctx.storage.transaction(operation)

    async def alarm(self):
        async with self.lock:
            # Persist another attempt before I/O; a crash cannot strand queued work.
            await self.ctx.storage.setAlarm(int(utc_now().timestamp() * 1000) + 60000)

            async def send(url, headers, body):
                response = await fetch(
                    url,
                    method="POST",
                    headers=headers,
                    body=body,
                    redirect="manual",
                    signal=AbortSignal.timeout(10000),
                )
                if response.body:
                    await response.body.cancel()
                return response.status

            await run_jobs(self.use_cases, send)
            await schedule(self.ctx.storage, self.repository, replace=True)
