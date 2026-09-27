import asyncio
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cf_tams.app import create_app
from cf_tams.jobs import run_jobs
from cf_tams.repository import SQLiteRepository
from cf_tams.storage import R2Storage, load_backends
from tamoss.application.use_cases import TamossUseCases
from tamoss.domain.model import utc_now
from tamoss.settings import Settings
from tests.tams.support import PRIMARY_BACKEND_ID, PRIMARY_BACKEND_LABEL


class SQLiteStorage:
    """Execute the production SQL against SQLite in CPython tests."""

    def __init__(self, path=":memory:"):
        self.connection = sqlite3.connect(
            path, isolation_level=None, check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.sql = self

    def exec(self, sql, *params):
        rows = [dict(row) for row in self.connection.execute(sql, params).fetchall()]
        return SimpleNamespace(toArray=lambda: rows)

    def transactionSync(self, operation):
        self.connection.execute("BEGIN")
        try:
            result = operation()
            self.connection.commit()
            return result
        except BaseException:
            self.connection.rollback()
            raise


class Bucket:
    def __init__(self):
        self.objects = {}
        self.fail_delete = False

    async def head(self, key):
        return (
            SimpleNamespace(size=len(self.objects[key]), etag="test-etag")
            if key in self.objects
            else None
        )

    async def delete(self, key):
        if self.fail_delete:
            raise OSError("injected R2 outage")
        self.objects.pop(key, None)


class TestR2Storage(R2Storage):
    @property
    def bucket(self):
        return self.bucket_for(self.repository.default_storage_backend().id)

    def write(self, object_id, data, *, backend=None):
        backend = backend or self.repository.default_storage_backend()
        self.bucket_for(backend.id).objects[
            self.key(self.repository.get_object(object_id).storage_key)
        ] = data

    def read(self, object_id, *, backend=None):
        record = self.repository.get_object(object_id)
        backend = backend or self.repository.default_storage_backend()
        return (
            self.bucket_for(backend.id).objects.get(self.key(record.storage_key))
            if record
            else None
        )


def make_use_cases(path=":memory:", backends=None):
    storage = SQLiteStorage(path)
    configured = backends or {
        "MEDIA": {
            "storage_id": str(PRIMARY_BACKEND_ID),
            "label": PRIMARY_BACKEND_LABEL,
            "bucket_name": "media",
            "default_storage": True,
        }
    }
    env = SimpleNamespace(
        **{binding: Bucket() for binding in configured},
        STORAGE_BACKENDS=json.dumps(configured),
        STORE_ID="test",
        R2_ENDPOINT="https://objects.example.test",
        R2_ACCESS_KEY_ID="test",
        R2_SECRET_ACCESS_KEY="test-secret",
    )
    repository = SQLiteRepository(storage, list(load_backends(env).values()))
    settings = Settings(
        webhook_allowed_hosts=[
            "example.com",
            "example.test",
            "receiver.example.test",
            "webhook.example.test",
            "hooks.example.test",
            "a.example.test",
            "z.example.test",
            "webhooks.example.test",
        ]
    )
    return TamossUseCases(
        repository=repository,
        object_storage=TestR2Storage(env, repository),
        settings=settings,
    )


@pytest.fixture
def tamoss_app(request):
    use_cases = make_use_cases(backends=getattr(request, "param", None))
    app = create_app(use_cases)

    @app.middleware("http")
    async def transaction(request, call_next):
        from tamoss.auth import Identity

        if request.headers.get("authorization") == "Bearer test-token":
            request.state.tamoss_identity = Identity(
                "test-client", frozenset({"tams-api/admin"})
            )
        repo = use_cases.repository
        repo.storage.connection.execute("BEGIN")
        repo.in_transaction = True
        try:
            response = await call_next(request)
            if response.status_code >= 400:
                repo.storage.connection.rollback()
            else:
                repo.storage.connection.commit()
            return response
        finally:
            repo.in_transaction = False

    def process_jobs():
        # Advance allocation age to exercise physical cleanup without waiting a day.
        repo = use_cases.repository
        for cleanup in repo.list_object_cleanups(statuses={"pending", "error"}):
            cleanup.object_created = utc_now() - timedelta(days=2)
            repo.save_object_cleanup(cleanup)
        before = len(repo.pending("delete_requests", {"created", "started", "error"}))

        async def send(*args):
            return 200

        asyncio.run(run_jobs(use_cases, send))
        for cleanup in repo.list_object_cleanups(statuses={"pending", "error"}):
            cleanup.object_created = utc_now() - timedelta(days=2)
            repo.save_object_cleanup(cleanup)
        asyncio.run(run_jobs(use_cases, send))
        return before

    app.state.process_jobs = process_jobs
    yield app
    use_cases.repository.storage.connection.close()


@pytest.fixture
def client(tamoss_app):
    with TestClient(
        tamoss_app, headers={"Authorization": "Bearer test-token"}
    ) as client:
        yield client
