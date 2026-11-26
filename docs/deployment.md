# Deploy and operate

Use these guides to operate a Cloudflare installation. For a first API exercise without an account, start with [your first local store](first-store.md). Look up defaults in the [configuration reference](reference.md).

## Set up tools

You need tar and [Aqua](https://aquaproj.github.io/docs/install/). Use Aqua's verified installer and add its binary directory to your shell's `PATH`. The [CI configuration](../.github/workflows/check.yml) records the tested Aqua version. From the repository root, run:

```sh
aqua install
npm ci
npm run runtime:install
```

Aqua pins Node.js and OpenTofu. npm installs Wrangler. The runtime installer verifies the archive against `runtime.lock.json` and extracts it into a directory named for its digest under `.runtime/`. To use a local archive, pass its path to `npm run runtime:install -- /path/to/archive.tar.gz`.

On a glibc Linux system that also has musl installed, Aqua can select the musl Node.js build. Set `AQUA_LIBC=glibc` in the shell before installing or running tools if that build fails to load. This uses Aqua's native [libc selection](https://aquaproj.github.io/docs/reference/registry-config/overrides/).

## Install

Complete [tool setup](#set-up-tools) and enable R2 in your Cloudflare account. Choose a profile for this installation.

| Profile | API authentication | Resources |
| --- | --- | --- |
| `free` | Shared HTTP Basic credentials with full store access | API Worker, SQLite Durable Object and private R2 buckets |
| `paid` (default) | OAuth client credentials and scoped JWTs | The same store, plus an OAuth Worker and D1 database |

Both profiles provide the same TAMS API. Free is available for evaluation. Production support requires Workers Paid. Account billing and spending caps are managed separately in Cloudflare. Check the [Workers](https://developers.cloudflare.com/workers/platform/limits/), [Durable Object](https://developers.cloudflare.com/durable-objects/platform/pricing/) and [R2](https://developers.cloudflare.com/r2/pricing/) allowances for your expected usage.

### Provision the resources

Set `deployment_profile` and `account_id` in `infra/terraform.tfvars`, using [terraform.tfvars.example](../infra/terraform.tfvars.example) as a starting point. For Paid, also set `auth_issuer` and `tams_audience` to the public HTTPS origins of the issuer and API, without trailing slashes. For additional buckets, custom domains, anonymous reads, browser origins and webhook receivers, see [installation inputs](reference.md#installation-inputs).

When deploying to an existing store, retain its Worker name, store ID, Durable Object migration history, backend UUIDs and bucket names. Preserve OpenTofu state between deployments. Configure a shared state backend if several operators manage the installation.

Put `CLOUDFLARE_API_TOKEN` in the ignored `.env` file. The token needs Workers Scripts and Workers R2 Storage write access. Paid also needs D1 write access. Account lookup requires Account Settings read and log inspection requires Workers Tail permissions. Protect credential files with mode 0600.

From the repository root, load the token and provision the selected profile.

```sh
set -a
. ./.env
set +a
tofu -chdir=infra init
tofu -chdir=infra apply
```

OpenTofu creates the buckets and generates the Wrangler configuration for each Worker. For Paid, it also creates the issuer database. Make configuration changes in the [files that own those settings](reference.md#configuration-ownership).

Create a separate, bucket-scoped [R2 token](https://developers.cloudflare.com/r2/api/tokens/) with Object Read & Write access to every configured bucket. Save its `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` in `.dev.vars`, following [.dev.vars.example](../.dev.vars.example). IP restrictions on this token apply to media clients using signed URLs. The deployment token can have its own IP restriction.

### Prepare authentication

**Free.** Add `BASIC_AUTH_USERNAME` and a separate random `BASIC_AUTH_PASSWORD` of at least 32 characters to `.dev.vars`. Then [deploy the API](#deploy-the-api). See [Basic authentication](#configure-basic-authentication) for credential requirements and rotation.

**Paid.** Save a separate random `BETTER_AUTH_SECRET` of at least 32 characters in `auth/.dev.vars`. Preserve this secret across deployments. It encrypts the issuer's signing keys. Apply the issuer migrations, then deploy the issuer.

```sh
npx wrangler d1 migrations apply DB --remote --config auth/wrangler.deploy.json
npx wrangler deploy --config auth/wrangler.deploy.json --secrets-file auth/.dev.vars
```

### Deploy the API

For either profile, deploy the packaged API Worker with its runtime secrets.

```sh
npx wrangler deploy --config wrangler.deploy.json --secrets-file .dev.vars
```

On a new account, run the first Wrangler deployment interactively. Wrangler prompts to register a `workers.dev` subdomain if needed. Using `--secrets-file` on that deployment uploads the secrets and lets Wrangler complete account setup. A separate `secret put` or `secret bulk` first would create a draft Worker and bypass that setup.

For Free, connect with your Basic credentials. For Paid, [provision an OAuth client](oauth.md#provision-clients). Then [check the installation](#check-an-installation).

## Configure Basic authentication

The Free profile accepts HTTP Basic credentials directly on the TAMS API. Store `BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD` alongside the R2 keys in `.dev.vars`, with mode 0600. Generate a unique random password; never reuse a Cloudflare deployment token, R2 key or OAuth client secret.

The username must contain 1–128 characters without a colon or control characters. The password must contain 32–1,024 characters without control characters. Credentials use UTF-8. Both values are required; missing or invalid server configuration returns 503. Incorrect credentials return 401 with a Basic challenge. The Worker accepts HTTPS, with HTTP permitted only for localhost development.

Configure a client's HTTP Basic username and password against the API URL. The account has administrator access to the whole store, and its username is recorded as the creator of new resources. There is no token endpoint, automatic expiry or separate access per user. Do not send these credentials with signed R2 media requests.

To rotate the password, replace it in the protected `.dev.vars`, deploy the API with `--secrets-file .dev.vars`, then update clients. Requests using the old password stop authenticating once the new deployment is active; already-issued media URLs retain their independent expiry. Keep a protected copy of the previous secret if rollout recovery requires it.

## Change deployment profile

To move Free to Paid, set `deployment_profile = "paid"`, supply the OAuth URLs and issuer secret, then follow the Paid installation steps. The plan must preserve every existing bucket and metadata identity. Deploy and provision the issuer before deploying the API. The API switches authentication when deployed, so update clients to OAuth at that point. Basic credentials are not accepted by Paid, even if their secret bindings remain. Remove those bindings and local copies after verification if rollback no longer needs them.

Changing an existing Paid installation to Free would remove its auth database from the desired resources. The `prevent_destroy` guard blocks that plan. Back up auth data and agree its retirement before using an authorised maintenance checkout to remove only the D1 destruction guard. Preserve the auth Worker configuration outside the generated path and prepare Basic credentials. Generate the Free API configuration with `tofu -chdir=infra apply -target=local_file.worker_config`, then deploy and verify the API with Basic authentication. Delete the unused auth Worker through Wrangler using the saved configuration. Review and apply the full OpenTofu plan to remove D1 and its generated configuration, then restore the guard. The targeted apply is only an intermediate migration step; finish with a full plan showing no changes. Keep storage guards and identities unchanged throughout. Account billing must be changed separately in Cloudflare.

## Add a storage backend

Set `storage_backends` in `infra/terraform.tfvars` to a map keyed by Worker binding. Each entry supplies `bucket_name`, a stable UUID in `storage_id`, and a unique `label`. Exactly one entry must set `default_storage = true`; optional `tags` maps tag names to lists of strings. Keep the original `MEDIA` entry when adding a bucket. The example file shows a second backend.

Run `tofu -chdir=infra plan` and check that existing buckets are preserved. Apply the plan, provision an R2 Object Read & Write signing token scoped to all configured buckets, update the two R2 secrets in `.dev.vars`, and deploy with the native Wrangler command above. Keep credentials outside OpenTofu inputs and state. Retain the previous signing token for at least one hour after deployment so existing media URLs remain usable, then revoke it. The deployment token is separate.

Check the [storage identity rules](reference.md#storage-backends) before updating a backend. Do not roll back to code that cannot read every configured backend after storing media in a new bucket.

Use `/service/storage-backends` to discover IDs. Include `storage_id` in `POST /flows/{flowId}/storage` to select a bucket; omitting it uses the configured default. To copy a registered Object, send `{"storage_id":"DESTINATION_UUID"}` to `POST /objects/{objectId}/instances`. A 201 response confirms durable enqueueing. Poll the Object's `get_urls` until the destination appears. Copies preserve bytes and metadata; the destination is advertised only after R2 confirms completion. Init Objects need separate copy requests.

Repeated pending copy requests are coalesced; a destination already holding the Object is rejected. Check the [copy size and retry limits](reference.md#scope-and-limits) before copying large Objects.

Delete an instance using `DELETE /objects/{objectId}/instances?storage_id=UUID`. The last instance cannot be deleted this way. Physical cleanup observes the allocation's 24-hour minimum lifetime and checks that a later copy has not restored the instance.

## Check an installation

`/healthz` is public. By default, `/readyz`, `/openapi.json` and `/service` require the selected profile's credentials. Enabling `wasm_anonymous_read` also allows public reads of these endpoints. `/readyz` checks SQLite and each configured R2 bucket. Use a disposable installation to [verify the API and media](#verify-the-api-and-media). Inspect request logs or infrastructure drift with:

```sh
npx --no-install wrangler tail --config wrangler.deploy.json
tofu -chdir=infra plan -detailed-exitcode
```

The plan returns exit code 0 when infrastructure matches its configuration, 2 when changes are present and 1 on an error.

## Update and recover

Run the [local checks](../CONTRIBUTING.md#run-local-checks) before deployment. Record the active versions with `npx wrangler deployments list --config wrangler.deploy.json` and, for Paid, the equivalent auth configuration command. Inspect and apply the OpenTofu plan after installing the pinned archive. For Paid, apply additive D1 migrations and deploy the issuer first. Deploy TAMS and validate authentication and playback for the selected profile.

Use `npx wrangler rollback VERSION_ID --config wrangler.deploy.json` to restore a recorded TAMS version; use the auth configuration to restore an issuer version. Rollback restores code and bindings, not database contents. Keep migrations compatible with the previous version during rollout. Record a D1 bookmark with `npx wrangler d1 time-travel info DB --config auth/wrangler.deploy.json` before future schema changes. Follow [Cloudflare's recovery procedure](https://developers.cloudflare.com/d1/reference/time-travel/) if restoration is required. Preserve the signing-key encryption secret with the database backup.

Review the [migration prerequisites](../CHANGELOG.md) before upgrading an existing installation.

## Decommission an installation

Obtain explicit approval to delete the installation and its data. Before proceeding, agree which media and metadata must be retained and complete their export or backup. Save the OpenTofu state, generated Worker configurations and resource inventory outside the installation. Retain `BETTER_AUTH_SECRET` with any D1 backup that must remain recoverable.

Stop ingest and client use. Load the deployment credential as described under [Install](#install), then delete the API Worker before its auth dependency:

```sh
npx --no-install wrangler delete --config wrangler.deploy.json
# Paid only:
npx --no-install wrangler delete --config auth/wrangler.deploy.json
```

Deleting the API Worker also deletes its Durable Object namespace and metadata. Verify both Workers and the namespace are absent using Cloudflare's resource inventory; deleting the Workers does not delete R2 or D1. Keep Wrangler's dependency checks enabled and investigate any remaining consumers instead of forcing deletion. If Wrangler fails while listing unrelated KV namespaces, use the [Worker deletion API](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/methods/delete/) for the verified account and Worker name with its default dependency checks.

Revoke the installation's R2 signing credential after media retention is complete, so outstanding upload URLs cannot refill the buckets. Empty every bucket listed in `storage_backends` using Cloudflare's native [Empty Bucket action](https://developers.cloudflare.com/r2/buckets/delete-buckets/), then verify that each is empty. Keep the deployment credential until resource cleanup finishes.

Only in the authorised teardown checkout, remove the `prevent_destroy` lifecycle guards from the R2 bucket and D1 resource definitions. Leave these guards enabled in the normal configuration. Plan destruction through OpenTofu, retaining its state throughout:

```sh
mkdir -p .local/tmp
tofu -chdir=infra plan -destroy -out=../.local/tmp/decommission.tfplan
```

Review the saved plan: it must contain only this installation's resources. Apply it after confirming that it matches the authorised teardown:

```sh
tofu -chdir=infra apply ../.local/tmp/decommission.tfplan
tofu -chdir=infra state list
```

Check that state is empty and confirm in Cloudflare that D1, every configured R2 bucket, both Workers and the Durable Object namespace are absent. Restore the lifecycle guards in the checkout, remove the saved plan and revoke credentials dedicated to the removed installation. Retain any required backups securely; do not remove resources from state as a substitute for deleting them.

## Run locally

Follow [your first local store](first-store.md) to run the packaged Worker locally with Basic credentials.

## Use media URLs

Create a Flow, allocate storage with `POST /flows/{flowId}/storage`, and PUT bytes to each returned `put_url.url` using **every returned header**. Uploads include a signed `If-None-Match: *` header to prevent overwriting an existing Object. A retry after a successful upload returns 412; register the uploaded Object instead of replacing it.

Register Segments only after uploads finish. The service checks R2 metadata before accepting controlled Objects. Request Segment listings with `presigned=true` for URLs usable without additional credentials. Signed URLs expire after one hour and support HTTP Range requests. A `presigned=false` URL requires separate S3 authentication against the private bucket; TAMS credentials do not authorise R2.

Once Flow deletion is accepted, new allocations and Segment registration for that Flow are rejected. Deletion removes metadata through alarms and retains physical bytes until the allocation is at least 24 hours old. Unregistered allocations expire after the same period. This leaves time for upload URLs to expire before cleanup. A deletion request becomes `done` when the selected metadata has been removed and physical cleanup has been durably queued. Physical cleanup continues independently; storage failures are retried.

## Enable webhooks

Set `webhook_allowed_hosts` in `infra/terraform.tfvars`. Entries accept exact hostnames or a leading `*.` for subdomains:

```hcl
webhook_allowed_hosts = [
  "hooks.example.org",
  "*.example.com",
]
```

Use domains whose subdomains you trust to receive events. A leading wildcard excludes the parent domain; add the parent separately if needed. See the [receiver rules](reference.md#webhook-receivers) for matching and URL restrictions.

Apply OpenTofu to generate the `WEBHOOK_ALLOWED_HOSTS` runtime variable in `wrangler.deploy.json`, then redeploy the TAMS Worker. The input and local Wrangler configuration both default to an empty allowlist. Include the checker's receiver hostname explicitly when qualifying webhooks. For local development, use a Wrangler override such as `--var 'WEBHOOK_ALLOWED_HOSTS:["hooks.example.com"]'`.

Retry registration and inspect Workers Logs if it fails. A rejected target logs its hostname without URL path, query string or credentials. Receivers should tolerate duplicate events after a crash.

## Configure browser access

Configure API and media CORS separately in the native `infra/terraform.tfvars` file. Both are disabled by default. For a browser application at `https://editor.example.com`, set:

```hcl
api_cors = {
  origins = ["https://editor.example.com"]
}
media_origins = ["https://editor.example.com"]
```

Origins include the scheme and optional port, without a path or trailing slash. HTTP origins work for local development. Apply OpenTofu and redeploy the API and, for Paid, the OAuth Worker. OpenTofu manages the R2 policy, the API's `API_CORS` setting and the issuer's `CORS_ORIGINS` setting. The issuer uses the same origins with its own GET/POST methods. Do not independently change the bucket policy through the dashboard or Wrangler.

Use the [CORS reference](reference.md#cors-defaults) to customise methods, headers, exposed headers and preflight caching. Preserve the signed upload headers in the media policy. Enable `allow_credentials` only when the application needs browser credential mode, using explicit origins.

API preflight requests are public. Actual requests follow the configured authentication and anonymous-read policy. CORS does not make the R2 bucket public or replace signed URLs. For direct local Worker development, set the equivalent JSON policy in the `API_CORS` Wrangler variable. Local development does not apply a remote R2 policy.

For example, start Wrangler with `--var 'API_CORS:{"origins":["http://localhost:3000"]}'`. Send an OPTIONS request with that Origin and inspect the access-control headers.

## Verify the API and media

Use a disposable store and authenticate with the profile's Basic credentials or a scoped OAuth token. Request `GET /readyz` and check for HTTP 200. Request `GET /service` and check that `api_version` is `8.2`.

1. Create a Flow and [allocate and upload a small media object](#use-media-urls). Include every header returned for the signed PUT.
2. Register a Segment referencing the uploaded object. Request `GET /flows/{flowId}/segments` with its time range and `presigned=true`. Check the returned object ID.
3. Send a GET to the returned R2 URL with `Range: bytes=0-3` for a sample at least four bytes long. Expect HTTP 206 and the first four bytes of the uploaded file. Keep API credentials on the API origin.
4. After redeployment, repeat the Segment lookup and use the fresh signed URL to read the same bytes. If the installation uses webhooks, register its configured receiver and verify delivery of a subscribed event.

## Maintain dependencies

To update TASM, pin the version and digest of a tested release in `runtime.lock.json`. Install the archive, apply OpenTofu, then apply any D1 migrations and deploy the Paid issuer before the API Worker. Retain the WasmStore class, migration tags, Worker name, store ID, backend UUIDs and bucket names when updating a populated Wasm installation.

Aqua pins Node.js and OpenTofu, npm pins Wrangler, and the OpenTofu lock file pins the providers. Runtime dependencies belong to the TASM release.
