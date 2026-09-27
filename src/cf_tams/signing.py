"""S3 query signing using the AWS Signature Version 4 canonical request."""

from datetime import UTC, datetime
from hashlib import sha256
from hmac import digest
from urllib.parse import quote, urlencode, urlsplit, urlunsplit


def presign(
    method, url, access_key, secret_key, *, headers=None, expires=3600, now=None
):
    if not 1 <= expires <= 604800:
        raise ValueError("Signature expiry must be between 1 second and 7 days")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Expected an HTTPS object URL without a query or fragment")
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    date = stamp[:8]
    scope = f"{date}/auto/s3/aws4_request"
    canonical_headers = {"host": parsed.netloc}
    canonical_headers.update(
        {
            key.lower().strip(): " ".join(value.split())
            for key, value in (headers or {}).items()
        }
    )
    signed_headers = ";".join(sorted(canonical_headers))
    query = urlencode(
        sorted(
            {
                "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
                "X-Amz-Credential": f"{access_key}/{scope}",
                "X-Amz-Date": stamp,
                "X-Amz-Expires": str(expires),
                "X-Amz-SignedHeaders": signed_headers,
            }.items()
        ),
        quote_via=quote,
        safe="~",
    )
    canonical = "\n".join(
        [
            method,
            parsed.path or "/",
            query,
            "".join(
                f"{key}:{canonical_headers[key]}\n" for key in sorted(canonical_headers)
            ),
            signed_headers,
            "UNSIGNED-PAYLOAD",
        ]
    )
    to_sign = (
        f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{sha256(canonical.encode()).hexdigest()}"
    )
    key = ("AWS4" + secret_key).encode()
    for value in (date, "auto", "s3", "aws4_request"):
        key = digest(key, value.encode(), "sha256")
    signature = digest(key, to_sign.encode(), "sha256").hex()
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            query + "&X-Amz-Signature=" + signature,
            "",
        )
    )
