import json

import pytest
from pydantic import ValidationError

from cf_tams.cors import CorsPolicy, load_policy, preflight, response_headers

ORIGIN = "https://editor.example.com"


def test_disabled_and_origin_selection():
    assert response_headers(CorsPolicy(), ORIGIN) == {}
    policy = CorsPolicy(origins=[ORIGIN])
    assert response_headers(policy, None) == {"Vary": "Origin"}
    assert response_headers(policy, "https://other.example.com") == {"Vary": "Origin"}
    headers = response_headers(policy, ORIGIN)
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Access-Control-Allow-Credentials" not in headers
    assert "X-Paging-Timerange" in headers["Access-Control-Expose-Headers"]
    assert "Location" in headers["Access-Control-Expose-Headers"]


def test_preflight_checks_method_and_headers():
    policy = CorsPolicy(origins=[ORIGIN], max_age_seconds=120)
    status, headers = preflight(policy, ORIGIN, "POST", "authorization, CONTENT-TYPE")
    assert status == 200
    assert headers["Access-Control-Max-Age"] == "120"
    assert "authorization" in headers["Access-Control-Allow-Headers"]
    assert "Access-Control-Request-Headers" in headers["Vary"]
    for origin, method, requested in [
        ("https://other.example.com", "POST", "authorization"),
        (ORIGIN, "PATCH", "authorization"),
        (ORIGIN, "POST", "X-Not-Allowed"),
    ]:
        assert preflight(policy, origin, method, requested)[0] == 400


@pytest.mark.parametrize(
    "existing,expected",
    [
        ("Accept-Encoding", "Accept-Encoding, Origin"),
        ("origin, Accept-Encoding", "origin, Accept-Encoding"),
        ("*", "*"),
    ],
)
def test_existing_vary_is_preserved(existing, expected):
    assert (
        response_headers(CorsPolicy(origins=[ORIGIN]), ORIGIN, existing)["Vary"]
        == expected
    )


def test_credentials_and_wildcard_headers():
    policy = CorsPolicy(origins=[ORIGIN], allow_credentials=True, headers=["*"])
    status, headers = preflight(policy, ORIGIN, "PUT", "Authorization, X-Client")
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    assert headers["Access-Control-Allow-Credentials"] == "true"
    assert headers["Access-Control-Allow-Headers"] == "authorization, x-client"
    assert (
        response_headers(CorsPolicy(origins=["*"]), ORIGIN)[
            "Access-Control-Allow-Origin"
        ]
        == "*"
    )


@pytest.mark.parametrize(
    "config",
    [
        {"origins": ["https://example.com/path"]},
        {"origins": ["https://user:password@example.com"]},
        {"origins": ["https://example.com?query"]},
        {"origins": ["https://example.com#fragment"]},
        {"origins": ["https://example.com:99999"]},
        {"origins": ["file://example.com"]},
        {"origins": ["*"], "allow_credentials": True},
        {"origins": "*"},
        {"methods": ["INVALID"]},
        {"headers": ["X-Test\r\nInjected: yes"]},
        {"max_age_seconds": -1},
        {"max_age_seconds": 0.5},
        {"allow_credentials": "false"},
        {"unknown": True},
    ],
)
def test_invalid_configuration(config):
    with pytest.raises(ValidationError):
        load_policy(json.dumps(config))


def test_local_origins_and_custom_settings():
    policy = load_policy(
        json.dumps(
            {
                "origins": ["http://localhost:3000", "http://[::1]:3000"],
                "methods": ["GET"],
                "headers": [],
                "expose_headers": [],
                "max_age_seconds": 0,
            }
        )
    )
    status, headers = preflight(policy, "http://[::1]:3000", "GET", "")
    assert status == 200
    assert headers["Access-Control-Max-Age"] == "0"
    assert "Access-Control-Expose-Headers" not in headers
