from asyncio import Semaphore, gather, timeout
from hashlib import sha256
import re
from urllib.parse import quote
from uuid import UUID
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from cf_tams.signing import presign

from tamoss.domain.model import ObjectStorageMetadata, StorageBackend
from tamoss.errors import BadRequest, ConfigurationError

URL_TTL_SECONDS = 3600
MAX_COPY_BYTES = 5 * 1024**3


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    storage_id: UUID
    bucket_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    label: str = Field(min_length=1)
    default_storage: bool = False
    tags: dict[str, list[str]] = Field(default_factory=dict)


def load_backends(env):
    configured = TypeAdapter(dict[str, BackendConfig]).validate_json(
        env.STORAGE_BACKENDS
    )
    reserved = {
        "AUTH",
        "STORE",
        "STORE_ID",
        "STORAGE_BACKENDS",
        "API_CORS",
        "API_TOKEN",
        "R2_ENDPOINT",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "WEBHOOK_ALLOWED_HOSTS",
    }
    if (
        sum(value.default_storage for value in configured.values()) != 1
        or any(
            len({getattr(value, field) for value in configured.values()})
            != len(configured)
            for field in ("storage_id", "bucket_name", "label")
        )
        or any(
            not re.fullmatch(r"[A-Z][A-Z0-9_]*", binding)
            or binding in reserved
            or not value.label.strip()
            for binding, value in configured.items()
        )
    ):
        raise ConfigurationError(
            "STORAGE_BACKENDS needs unique identities and one default."
        )
    return {
        binding: StorageBackend(
            id=value.storage_id,
            label=value.label,
            provider="cloudflare",
            region="auto",
            store_product="r2",
            default_storage=value.default_storage,
            bucket_name=value.bucket_name,
            endpoint_url=env.R2_ENDPOINT.rstrip("/"),
            tags=value.tags,
        )
        for binding, value in configured.items()
    }


def copy_etag(xml):
    root = ElementTree.fromstring(xml)
    namespace = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    if root.tag not in {"CopyObjectResult", namespace + "CopyObjectResult"}:
        raise RuntimeError("R2 did not return a successful CopyObject result")
    etag = root.findtext(namespace + "ETag") or root.findtext("ETag")
    if not etag:
        raise RuntimeError("R2 CopyObject result has no ETag")
    return etag.strip('"')


class R2Storage:
    def __init__(self, env, repository):
        self.buckets = {
            backend.id: getattr(env, binding)
            for binding, backend in load_backends(env).items()
        }
        self.repository = repository
        self.endpoint = env.R2_ENDPOINT.rstrip("/")
        self.prefix = sha256(env.STORE_ID.encode()).hexdigest()[:32] + "/"
        self.access_key = env.R2_ACCESS_KEY_ID
        self.secret_key = env.R2_SECRET_ACCESS_KEY
        self.metadata = {}

    def key(self, storage_key):
        return self.prefix + storage_key

    def bucket_for(self, storage_id):
        return self.buckets[storage_id]

    def stored_url(self, backend, storage_key):
        return f"{self.endpoint}/{quote(backend.bucket_name, safe='')}/{self.key(storage_key)}"

    def object_url(self, object_id, backend):
        record = self.repository.get_object(object_id)
        if record is None:
            raise BadRequest("The Object allocation does not exist.")
        return self.stored_url(backend, record.storage_key)

    def sign(self, method, url, headers=None):
        if not self.access_key or not self.secret_key:
            raise ConfigurationError("R2 signing credentials are missing.")
        return presign(
            method,
            url,
            self.access_key,
            self.secret_key,
            headers=headers,
            expires=URL_TTL_SECONDS,
        )

    def build_put_request(self, *, object_id, content_type, backend, presigned):
        headers = {"Content-Type": content_type, "If-None-Match": "*"}
        url = self.object_url(object_id, backend)
        return {
            "url": self.sign("PUT", url, headers) if presigned else url,
            "content-type": content_type,
            "headers": headers,
        }

    def build_get_urls_batch(self, requests):
        result = {}
        for request in requests:
            url = self.object_url(request.object_id, request.backend)
            urls = []
            if request.include_direct:
                urls.append(
                    {"url": url, "label": request.backend.label, "presigned": False}
                )
            if request.include_presigned:
                urls.append(
                    {
                        "url": self.sign("GET", url),
                        "label": request.backend.label,
                        "presigned": True,
                    }
                )
            result[(request.backend.id, request.object_id)] = urls
        return result

    async def prepare_registration(self, body):
        """Read R2 metadata while the store lock protects allocation identities."""
        self.metadata = {}
        payloads = body if isinstance(body, list) else [body]
        if len(payloads) > 1000:
            raise BadRequest("At most 1000 Segments may be registered per request.")
        object_ids = {
            value
            for payload in payloads
            if isinstance(payload, dict)
            for field in ("object_id", "init_object_id")
            if isinstance(value := payload.get(field), str)
        }
        records = self.repository.get_objects(object_ids)
        for record in list(records.values()):
            if record.init_object_id and record.init_object_id not in records:
                init = self.repository.get_object(record.init_object_id)
                if init is not None:
                    records[init.id] = init
        slots = Semaphore(6)

        async def head(record, backend):
            if record.referenced_by_flows:
                return
            async with slots:
                result = await self.bucket_for(backend.id).head(
                    self.key(record.storage_key)
                )
            self.metadata[(record.id, record.created, backend.id)] = (
                ObjectStorageMetadata(content_length=result.size, etag=result.etag)
                if result is not None
                else None
            )

        await gather(
            *(
                head(record, instance.storage_backend)
                for record in records.values()
                for instance in record.instances
                if instance.controlled and instance.storage_backend
            )
        )

    def object_metadata(self, object_id, *, backend=None):
        record = self.repository.get_object(object_id)
        if record is None:
            return None
        backend = backend or self.repository.default_storage_backend()
        key = (record.id, record.created, backend.id)
        if key not in self.metadata:
            raise RuntimeError("Controlled Object was not checked against R2")
        return self.metadata[key]

    async def copy(self, job):
        """Copy within R2, retaining the source's metadata and exact bytes."""
        from js import AbortSignal
        from workers import fetch

        source = self.repository.get_storage_backend(job.source_storage_backend_id)
        destination = self.repository.get_storage_backend(
            job.destination_storage_backend_id
        )
        key = self.key(job.storage_key)
        async with timeout(30):
            original = await self.bucket_for(source.id).head(key)
            if original is None or original.size > MAX_COPY_BYTES:
                raise RuntimeError(
                    "R2 copy requires an existing Object no larger than 5 GiB"
                )
            headers = {
                "x-amz-copy-source": f"/{quote(source.bucket_name, safe='')}/{quote(key, safe='/')}",
                "x-amz-copy-source-if-match": f'"{original.etag}"',
                "x-amz-metadata-directive": "COPY",
            }
            url = self.sign(
                "PUT", self.stored_url(destination, job.storage_key), headers
            )
            response = await fetch(
                url,
                method="PUT",
                headers=headers,
                redirect="manual",
                signal=AbortSignal.timeout(30000),
            )
            if response.status != 200:
                raise RuntimeError(f"R2 CopyObject returned HTTP {response.status}")
            # CopyObject can report an error in a successful HTTP response.
            reader = response.body.getReader()
            body = bytearray()
            try:
                while True:
                    chunk = await reader.read()
                    if chunk.done:
                        break
                    if len(body) + chunk.value.byteLength > 65536:
                        raise RuntimeError("R2 CopyObject response exceeds 64 KiB")
                    body.extend(chunk.value.to_bytes())
            finally:
                await reader.cancel()
                reader.releaseLock()
            etag = copy_etag(bytes(body))
            copied = await self.bucket_for(destination.id).head(key)
            if (
                copied is None
                or copied.size != original.size
                or copied.etag != etag
                or etag != original.etag
            ):
                raise RuntimeError("R2 copy metadata does not match the source")
