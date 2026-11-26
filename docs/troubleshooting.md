# Troubleshooting

Record the affected Worker, request method and path, HTTP status, time and Cloudflare request ID. Check browser network responses and Worker logs before changing configuration:

```sh
npx --no-install wrangler tail --config wrangler.deploy.json
npx --no-install wrangler tail --config auth/wrangler.deploy.json
```

Run each tail in a separate terminal; the auth Worker exists only for Paid. Use the generated configuration for the installation being investigated. Remove Authorization headers, cookies, credentials and signed media URLs before sharing evidence.

## Basic credentials rejected

Free expects HTTP Basic credentials directly on the API. A 401 means the username, password or authentication scheme is incorrect. A 503 with `configuration_error` means the deployed Basic secrets or `AUTH_MODE` are invalid; check the generated profile configuration and [credential constraints](deployment.md#configure-basic-authentication). A non-local HTTP request is rejected with 403; use HTTPS.

Apply OpenTofu and redeploy the API after changing the profile. Setting secrets alone does not switch authentication. Paid rejects Basic API credentials even when their bindings remain from an earlier deployment.

## OAuth method mismatch

`client registered for client_secret_basic cannot use client_secret_post` means the client sent credentials using a different method from its registration. Configure the client to use Basic authentication, or provision a new client with `--method client_secret_post`. Rotating a secret does not change the registered method. Follow the separate [TAMSTool](oauth.md#tamstool) and [Cutting Room](oauth.md#cutting-room) examples.

## Missing permissions

A 403 with `insufficient_scope` means the token lacks permission for the requested operation. List clients and update the relevant client's scopes using [the operator CLI](oauth.md#rotate-or-disable-credentials), then obtain a new token. Existing tokens keep their original scopes until expiry.

Write does not imply read. An editor usually needs both; Flow and Segment deletion additionally needs delete. Creating or replacing a webhook requires both read and write, or admin. Deleting a webhook requires write or admin. Check the [permission table](reference.md#oauth-permissions) before granting administrator access.

## Webhook registration returns 400

An application's generic message about write access can also accompany an invalid receiver URL. Check the actual TAMS status and response, then inspect the TAMS Worker log for the rejected hostname. Logs omit the path and query string.

Add the receiver's hostname to `webhook_allowed_hosts` in `infra/terraform.tfvars`, apply OpenTofu and redeploy the TAMS Worker. This is the server receiving callbacks; it may differ from the application's browser origin. Use [the allowlist rules](reference.md#webhook-receivers) to check wildcard matching. An allowed hostname still needs a public HTTPS receiver without a custom port or redirect.

## Browser CORS errors

Find the failed request in the network tab. The OAuth Worker, TAMS Worker and R2 bucket each have a CORS policy:

| Failed request | Check |
| --- | --- |
| Token POST or its OPTIONS request | Browser origin in `api_cors.origins`, auth Worker redeployed, and registered client method. |
| TAMS request or its OPTIONS request | Browser origin, requested method and headers in `api_cors`. |
| R2 GET, PUT or OPTIONS | Browser origin in `media_origins`; method and headers in `media_cors`. |

Copy the browser's `Origin` value, including scheme and port, without a URL path or trailing slash. For a page under `https://cuttingroom.github.io/tamstool/`, the origin is `https://cuttingroom.github.io`.

Follow [Configure browser access](deployment.md#configure-browser-access), apply OpenTofu and redeploy the API and, for Paid, the OAuth Worker. Test again after any cached preflight expires. API and OAuth preflights do not need bearer tokens; actual TAMS requests require the configured authentication method. A failed authenticated request can also be hidden by a missing CORS header, so inspect server logs for its underlying status.

R2 requests must use the signed URL without a TAMS Authorization header. Upload requests must include every header returned with `put_url`, including `If-None-Match`. Preserve these headers in the media CORS policy.

## Expired tokens or unavailable verification

After an access token expires, obtain another with the client credentials grant. Refresh tokens are not issued. A 401 can also mean the token targets a different issuer or audience. Check the installation's public origins and token endpoint, and verify the client clock.

A 503 indicates unavailable verification rather than insufficient scope. Inspect the auth Worker, its D1 binding and the API's `AUTH` service binding. Do not replace `BETTER_AUTH_SECRET` to fix an outage: existing signing keys depend on it. See [Update and recover](deployment.md#update-and-recover).

## Signed media requests fail

Request fresh URLs if their independent one-hour lifetime has elapsed. Do not alter their path or query string. TAMS credentials cannot authorise a private R2 URL returned with `presigned=false`; request `presigned=true` for playback.

For upload signature failures, send every returned header with its original value. A 412 from a repeated conditional PUT means an Object already exists at that allocation; confirm the earlier upload and register it instead of overwriting it.

If URLs fail for other users but work from the deployment machine, inspect the signing credential's bucket scope and client IP restrictions. Use the separate bucket-scoped credential described under [Install](deployment.md#install). After changing it, upload the R2 Worker secrets and request new URLs. Local R2 emulation cannot serve S3 presigned transfers.

## Playback stalls or audio and video drift

Check transport first: each media request should complete with 200, or 206 for a byte range. Investigate 401/403 responses, CORS failures, expired URLs and missing init Objects before inspecting timestamps.

If bytes arrive but playback stalls after a Segment, inspect the player's media errors, Segment timeranges and the encoded media timestamps. Check that init Objects match the media and that timestamp discontinuities are handled by the player. For audio/video drift, compare both Flows' TAMS timeranges with their encoded presentation timestamps, durations and sample rates. A successful download alone does not establish that those timelines align.

An API checker validates its published contract checks; it does not exercise every player, codec, ingest pipeline or timestamp discontinuity. Reproduce the issue with a short media sample and the intended player, recording whether the fault starts during transfer, decoding or timeline assembly. Include redacted evidence in a [bug report](https://github.com/livewyer-ops/cf-tams/issues).
