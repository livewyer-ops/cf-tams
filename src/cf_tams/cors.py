"""CORS policy for the Worker HTTP boundary."""

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Method = Literal["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
Header = Annotated[str, Field(pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")]


class CorsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    origins: list[str] = []
    methods: list[Method] = ["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"]
    headers: list[Header] = ["Authorization", "Content-Type"]
    expose_headers: list[Header] = [
        "Location",
        "Link",
        "WWW-Authenticate",
        "X-Paging-Limit",
        "X-Paging-Count",
        "X-Paging-Reverse-Order",
        "X-Paging-NextKey",
        "X-Paging-Timerange",
    ]
    max_age_seconds: int = Field(default=3600, ge=0)
    allow_credentials: bool = False

    @field_validator("origins")
    @classmethod
    def valid_origins(cls, origins):
        for origin in origins:
            if origin == "*":
                continue
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or any(c.isspace() for c in origin)
                or parsed.port == 0
                or origin != f"{parsed.scheme}://{parsed.netloc}"
            ):
                raise ValueError(
                    "Origins must be HTTP(S) origins without paths or credentials"
                )
        return origins

    @model_validator(mode="after")
    def credential_origins(self):
        if self.allow_credentials and "*" in self.origins:
            raise ValueError("Credentialed CORS requires explicit origins")
        return self


@lru_cache(maxsize=1)
def load_policy(raw: str) -> CorsPolicy:
    return CorsPolicy.model_validate_json(raw)


def response_headers(
    policy: CorsPolicy, origin: str | None, vary: str = ""
) -> dict[str, str]:
    if not policy.origins:
        return {}
    headers = {}
    if "*" not in policy.origins:
        tokens = {v.strip().lower() for v in vary.split(",")}
        headers["Vary"] = (
            vary
            if tokens & {"origin", "*"}
            else ", ".join(filter(None, [vary, "Origin"]))
        )
    if origin and (origin in policy.origins or "*" in policy.origins):
        headers["Access-Control-Allow-Origin"] = (
            "*" if "*" in policy.origins else origin
        )
        if policy.allow_credentials:
            headers["Access-Control-Allow-Credentials"] = "true"
        if policy.expose_headers:
            headers["Access-Control-Expose-Headers"] = ", ".join(policy.expose_headers)
    return headers


def preflight(policy: CorsPolicy, origin: str, method: str, requested_headers: str):
    headers = response_headers(policy, origin)
    headers["Vary"] = (
        "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"
    )
    allowed_headers = {h.lower() for h in policy.headers} | {
        "accept",
        "accept-language",
        "content-language",
        "content-type",
    }
    requested = [h.strip().lower() for h in requested_headers.split(",") if h.strip()]
    if (
        "Access-Control-Allow-Origin" not in headers
        or method not in policy.methods
        or ("*" not in allowed_headers and not set(requested) <= allowed_headers)
    ):
        return 400, headers
    headers["Access-Control-Allow-Methods"] = ", ".join(policy.methods)
    headers["Access-Control-Allow-Headers"] = ", ".join(
        requested if "*" in allowed_headers else sorted(allowed_headers)
    )
    headers["Access-Control-Max-Age"] = str(policy.max_age_seconds)
    return 200, headers
