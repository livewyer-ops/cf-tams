import asyncio

import pytest

from cf_tams.jobs import run_jobs
from tamoss.application.webhooks import (
    WebhookEgressError,
    WebhookEgressPolicy,
    validate_webhook_url,
)
from tests.tams.support import create_video_flow, webhook_payload


@pytest.mark.parametrize(
    "pattern,url,allowed",
    [
        ("hooks.example.com", "https://hooks.example.com/hook", True),
        ("HOOKS.EXAMPLE.COM.", "https://hooks.example.com/hook", True),
        ("*.example.com", "https://hooks.example.com/hook", True),
        ("*.EXAMPLE.COM.", "https://HOOKS.EU.EXAMPLE.COM./hook", True),
        ("*.example.com", "https://hooks.example.com:443/hook", True),
        ("*.example.com", "https://example.com/hook", False),
        ("*.example.com", "https://evil-example.com/hook", False),
        ("*.example.com", "https://example.com.evil.test/hook", False),
        ("*.example.com", "https://.example.com/hook", False),
        ("*.example.com", "https://a..example.com/hook", False),
        ("*.example.com", "https://*.example.com/hook", False),
        ("*.example.com", "http://hooks.example.com/hook", False),
        ("*.example.com", "https://hooks.example.com:8443/hook", False),
        ("*.example.com", "https://hooks.example.com/hook#fragment", False),
        ("*.example.com", "https://user:secret@hooks.example.com/hook", False),
        ("*", "https://hooks.example.com/hook", False),
        ("*example.com", "https://hooks.example.com/hook", False),
        ("hooks.*.com", "https://hooks.example.com/hook", False),
        ("*.*.example.com", "https://hooks.eu.example.com/hook", False),
        ("*..example.com", "https://hooks.example.com/hook", False),
        ("*.com", "https://example.com/hook", False),
        ("*.8.8", "https://8.8.8.8/hook", False),
        ("*.8.8.8.8", "https://hooks.8.8.8.8/hook", False),
        ("*.0.0.1", "https://127.0.0.1/hook", False),
        ("127.0.0.1", "https://127.0.0.1/hook", False),
        ("*.google.internal", "https://metadata.google.internal/hook", False),
        ("*.svc.cluster.local", "https://receiver.svc.cluster.local/hook", False),
        ("8.8.8.8", "https://8.8.8.8/hook", True),
    ],
)
def test_webhook_host_patterns(pattern, url, allowed):
    policy = WebhookEgressPolicy(allowed_hosts=(pattern,))
    if allowed:
        validate_webhook_url(url, egress_policy=policy)
    else:
        with pytest.raises(ValueError):
            validate_webhook_url(url, egress_policy=policy)


def test_rejected_webhook_logs_only_hostname(caplog):
    with pytest.raises(WebhookEgressError):
        validate_webhook_url(
            "https://rejected.example.com/secret-path?key=secret-value"
        )
    assert "hostname='rejected.example.com'" in caplog.text
    assert "secret-path" not in caplog.text
    assert "secret-value" not in caplog.text


@pytest.mark.parametrize("revoke_before_delivery", [False, True])
def test_wildcard_registration_update_and_delivery(
    client, tamoss_app, revoke_before_delivery
):
    use_cases = tamoss_app.state.tamoss_use_cases
    use_cases.settings.webhook_allowed_hosts = ["*.example.com"]
    body = webhook_payload(
        url="https://hooks.example.com/receive", events=["flows/created"]
    )
    created = client.post("/service/webhooks", json=body)
    assert created.status_code == 201
    body.update(
        id=created.json()["id"],
        status="created",
        url="https://hooks.eu.example.com/receive",
    )
    updated = client.put(f"/service/webhooks/{body['id']}", json=body)
    assert updated.status_code == 201
    create_video_flow(client)
    if revoke_before_delivery:
        use_cases.settings.webhook_allowed_hosts = []
    calls = []

    async def send(url, headers, payload):
        calls.append(url)
        return 204

    asyncio.run(run_jobs(use_cases, send))
    deliveries = use_cases.repository.list_webhook_deliveries()
    assert len(deliveries) == 1
    assert deliveries[0].status == ("dead" if revoke_before_delivery else "done")
    assert calls == ([] if revoke_before_delivery else [body["url"]])
