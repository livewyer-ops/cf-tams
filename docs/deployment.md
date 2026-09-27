# Deploy and operate

Use these guides to operate a Cloudflare installation. For a first API exercise without an account, start with [your first local store](first-store.md). Look up defaults in the [configuration reference](reference.md).

## Set up tools

Install [Aqua](https://aquaproj.github.io/docs/install/) using its verified installer and add its binary directory to your shell's `PATH`. The [CI configuration](../.github/workflows/check.yml) records the tested Aqua version. From the repository root, run:

```sh
aqua install
npm ci
uv sync --locked
```

[aqua.yaml](../aqua.yaml) pins Node.js, uv and OpenTofu, with verified downloads recorded in `aqua-checksums.json`. uv installs the Python interpreter selected by [.python-version](../.python-version). npm supplies the locked Wrangler executable. The tool packages cover Linux and macOS on amd64 and arm64; CI runs on Linux amd64. Git and a POSIX shell are also required. Application dependencies and tools install without system-wide package changes.

On a glibc Linux system that also has musl installed, Aqua can select the musl Node.js build. Set `AQUA_LIBC=glibc` in the shell before installing or running tools if that build fails to load. This uses Aqua's native [libc selection](https://aquaproj.github.io/docs/reference/registry-config/overrides/).

## Install

Complete [tool setup](#set-up-tools). A Cloudflare account with Workers Paid and R2 enabled, an activated billing account and deployment credentials are prerequisites to installation.

Configure the installation inputs:

- Set `account_id`, `auth_issuer` and `tams_audience` in `infra/terraform.tfvars`, following [terraform.tfvars.example](../infra/terraform.tfvars.example). The issuer and audience are the public HTTPS origins of the auth and TAMS Workers, without trailing slashes. `storage_backends` defaults to the original `MEDIA` bucket; `media_origins` defaults to no browser access.
- Set `webhook_allowed_hosts` in the same file to permit webhook receivers. It defaults to an empty list, which rejects all registrations; see [Enable webhooks](#enable-webhooks).
- Put `CLOUDFLARE_API_TOKEN` in the ignored `.env` file and load it into the process environment for both tools. It needs Workers Scripts, D1 and Workers R2 Storage write access; account lookup and log inspection also use Account Settings read and Workers Tail permissions.
- Put `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` in `.dev.vars`, following `.dev.vars.example`. They need Object Read & Write access to the installation's configured buckets. On a new installation, create this signing credential after OpenTofu has provisioned the buckets and before deploying the TAMS Worker.
- Put a separately generated `BETTER_AUTH_SECRET` of at least 32 random characters in `auth/.dev.vars`. Preserve it across deployments; it encrypts the issuer's signing keys. OAuth client secrets are provisioned separately through the [operator CLI](oauth.md).

Use a separate, bucket-scoped [R2 token](https://developers.cloudflare.com/r2/api/tokens/) for signing media URLs. Its client IP restrictions apply to clients using those URLs, so a token restricted to the deployment machine prevents other clients from uploading or downloading media. The deployment token can retain its own IP restriction.

`CLOUDFLARE_API_TOKEN` is the only required deployment environment variable. OpenTofu writes the account ID into generated Wrangler configuration. Each Worker's `.dev.vars` supplies runtime secrets through `--secrets-file`; those values do not need to be exported. Protect secret files with mode 0600.

From the repository root, run:

```sh
set -a
. ./.env
set +a
tofu -chdir=infra init
tofu -chdir=infra apply
```

For a new installation, now create the bucket-scoped signing credential and save its keys in `.dev.vars`. Then apply the auth schema and deploy:

```sh
npx wrangler d1 migrations apply DB --remote --config auth/wrangler.deploy.json
npx wrangler deploy --config auth/wrangler.deploy.json --secrets-file auth/.dev.vars
npx wrangler deploy --config wrangler.deploy.json --secrets-file .dev.vars
```

OpenTofu provisions private R2 storage and the auth D1 database, then generates both Worker configurations. Apply D1 migrations and deploy the auth Worker before its TAMS consumer. Wrangler's native build hook prepares locked Python dependencies. Provision clients using the [OAuth guide](oauth.md) before testing the API.

On an account without a `workers.dev` subdomain, the first Wrangler deployment prompts to register one before uploading the Worker. Existing subdomains are reused. Upload secrets with `--secrets-file` on the first deployment; a separate `secret put` or `secret bulk` command creates a draft Worker and bypasses Wrangler's first-deployment account setup. New accounts need an interactive first deployment before unattended CI deployments.

Set installation inputs in `infra/terraform.tfvars` and application settings in the base Wrangler files. OpenTofu generates both deployment configurations; do not edit those outputs. See [configuration ownership](reference.md#configuration-ownership) for the division between tools and files.

Preserve OpenTofu state between deployments and configure a shared state backend before multiple operators manage the installation. Keep `STORE_ID`, backend identities and Durable Object migration history stable. Bucket and auth database destruction are guarded by `prevent_destroy`.

## Add a storage backend

Set `storage_backends` in `infra/terraform.tfvars` to a map keyed by Worker binding. Each entry supplies `bucket_name`, a stable UUID in `storage_id`, and a unique `label`. Exactly one entry must set `default_storage = true`; optional `tags` maps tag names to lists of strings. Keep the original `MEDIA` entry when adding a bucket. The example file shows a second backend.

Run `tofu -chdir=infra plan` and check that existing buckets are preserved. Apply the plan, provision an R2 Object Read & Write signing token scoped to all configured buckets, update the two R2 secrets in `.dev.vars`, and deploy with the native Wrangler command above. Keep credentials outside OpenTofu inputs and state. Retain the previous signing token for at least one hour after deployment so existing media URLs remain usable, then revoke it. The deployment token is separate.

Check the [storage identity rules](reference.md#storage-backends) before updating a backend. Do not roll back to code that cannot read every configured backend after storing media in a new bucket.

Use `/service/storage-backends` to discover IDs. Include `storage_id` in `POST /flows/{flowId}/storage` to select a bucket; omitting it uses the configured default. To copy a registered Object, send `{"storage_id":"DESTINATION_UUID"}` to `POST /objects/{objectId}/instances`. A 201 response confirms durable enqueueing. Poll the Object's `get_urls` until the destination appears. Copies preserve bytes and metadata; the destination is advertised only after R2 confirms completion. Init Objects need separate copy requests.

Repeated pending copy requests are coalesced; a destination already holding the Object is rejected. Check the [copy size and retry limits](reference.md#scope-and-limits) before copying large Objects.

Delete an instance using `DELETE /objects/{objectId}/instances?storage_id=UUID`. The last instance cannot be deleted this way. Physical cleanup observes the allocation's 24-hour minimum lifetime and checks that a later copy has not restored the instance.

## Check an installation

`/healthz` is public; `/readyz`, `/openapi.json` and `/service` require an OAuth access token. `/readyz` checks metadata access. The [live integration tests](../CONTRIBUTING.md#test-a-deployed-installation) verify the media path. Inspect request logs or infrastructure drift with:

```sh
npx --no-install wrangler tail --config wrangler.deploy.json
tofu -chdir=infra plan -detailed-exitcode
```

The plan returns exit code 0 when infrastructure matches its configuration, 2 when changes are present and 1 on an error.

## Update and recover

Run the [local checks](../CONTRIBUTING.md#run-local-checks) before deployment. Record the active versions with `npx wrangler deployments list --config wrangler.deploy.json` and the equivalent auth configuration command. Inspect the OpenTofu plan, apply additive D1 migrations, deploy the issuer, then deploy TAMS and validate real token issuance and playback.

Use `npx wrangler rollback VERSION_ID --config wrangler.deploy.json` to restore a recorded TAMS version; use the auth configuration to restore an issuer version. Rollback restores code and bindings, not database contents. Keep migrations compatible with the previous version during rollout. Record a D1 bookmark with `npx wrangler d1 time-travel info DB --config auth/wrangler.deploy.json` before future schema changes. Follow [Cloudflare's recovery procedure](https://developers.cloudflare.com/d1/reference/time-travel/) if restoration is required. Preserve the signing-key encryption secret with the database backup.

Review the [migration prerequisites](../CHANGELOG.md) before upgrading an existing installation.

## Decommission an installation

Obtain explicit approval to delete the installation and its data. Before proceeding, agree which media and metadata must be retained and complete their export or backup. Save the OpenTofu state, generated Worker configurations and resource inventory outside the installation. Retain `BETTER_AUTH_SECRET` with any D1 backup that must remain recoverable.

Stop ingest and client use. Load the deployment credential as described under [Install](#install), then delete the API Worker before its auth dependency:

```sh
npx --no-install wrangler delete --config wrangler.deploy.json
npx --no-install wrangler delete --config auth/wrangler.deploy.json
```

Deleting the API Worker also deletes its Durable Object namespace and metadata. Verify both Workers and the namespace are absent using Cloudflare's resource inventory; deleting the Workers does not delete R2 or D1. Keep Wrangler's dependency checks enabled and investigate any remaining consumers instead of forcing deletion. See the [Worker deletion API](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/methods/delete/).

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

Follow [your first local store](first-store.md) for credentials, local D1 migrations, client provisioning and the Worker commands. Keep local development in a separate checkout from deployment credentials.

Wrangler connects the service binding between the auth and TAMS sessions. Local resources persist under each Worker's `.wrangler/state`. The base configuration uses issuer `http://127.0.0.1:8788` and audience `http://127.0.0.1:8787`. Deployments use the generated configurations; their remote D1 binding is intended for explicit remote administration. See [Cloudflare's multiple-Worker development guide](https://developers.cloudflare.com/workers/local-development/multi-workers/).

## Use media URLs

Create a Flow, allocate storage with `POST /flows/{flowId}/storage`, and PUT bytes to each returned `put_url.url` using **every returned header**. Uploads include a signed `If-None-Match: *` header to prevent overwriting an existing Object. A retry after a successful upload returns 412; register the uploaded Object instead of replacing it.

Register Segments only after uploads finish. The service checks R2 metadata before accepting controlled Objects. Request Segment listings with `presigned=true` for URLs usable without additional credentials. Signed URLs expire after one hour and support HTTP Range requests. A `presigned=false` URL requires separate S3 authentication against the private bucket; the TAMS bearer token does not authorise R2.

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

Origins include the scheme and optional port, without a path or trailing slash. HTTP origins work for local development. Apply OpenTofu and redeploy both Workers. OpenTofu manages the R2 policy, the API's `API_CORS` setting and the issuer's `CORS_ORIGINS` setting. The issuer uses the same origins with its own GET/POST methods. Do not independently change the bucket policy through the dashboard or Wrangler.

Use the [CORS reference](reference.md#cors-defaults) to customise methods, headers, exposed headers and preflight caching. Preserve the signed upload headers in the media policy. Enable `allow_credentials` only when the application needs browser credential mode, using explicit origins.

API preflight requests do not require a bearer token; actual requests still do. CORS does not make the R2 bucket public or replace signed URLs. For direct local Worker development, set the equivalent JSON policy in the `API_CORS` Wrangler variable. Local development does not apply a remote R2 policy.

For example, start Wrangler with `--var 'API_CORS:{"origins":["http://localhost:3000"]}'`. Set `TAMS_TEST_ORIGIN=http://localhost:3000` alongside the existing live-test URL and token to run the Worker CORS integration check. CI uses this configuration automatically.

## Qualify the API

Use a disposable installation and provision a dedicated checker client with `tams-api/admin`. Permit the checker's callback hostname through [the webhook allowlist](#enable-webhooks). Open a deployed [TAMS Checker](https://github.com/livewyer-ops/tams-checker), enter this installation's HTTPS URL and select OAuth client credentials. Supply its token endpoint, client ID and secret using the registered authentication method. Complete Turnstile and run the TAMS 8.2 API checks. Keep the browser open until execution and cleanup finish, then export the report. Record both deployed Worker versions with the report so it identifies the candidate tested.

The checker creates and cleans up its own resources. Immutable test Profiles remain because BBC TAMS provides no Profile deletion operation; the report also lists any other cleanup residuals. It does not update shared Service metadata. A complete Pass covers the checker's published API scope, including immediate media transfer and advertised webhooks. Elapsed URL and Object lifetimes, physical reclamation and its other documented exclusions are outside that verdict.

For command-line runs, follow the [checker CLI workflow](https://github.com/livewyer-ops/tams-checker/blob/main/docs/run-checks.md). Standalone execution runs the checks locally without browser verification and excludes webhook management and delivery. To include webhooks, select a deployed checker with `--validator` and supply its required fresh browser verification token. Record the selected scope with the report.

## Maintain dependencies

Keep all lockfiles in version control. `uv.lock` covers Python dependencies; `pylock.toml` covers Pyodide packages; `package-lock.json` pins Wrangler and the auth dependencies; `infra/.terraform.lock.hcl` pins the OpenTofu providers. Aqua tool versions and checksums are shared by local setup and CI. For an approved tool update, edit `aqua.yaml`, run `aqua update-checksum` and review both files. Update the Python interpreter through `.python-version` within the compatibility range in `pyproject.toml`. Refresh dependencies explicitly and repeat the local runtime checks before deploying.

Follow the installation order for updates: apply OpenTofu, apply D1 migrations, then deploy both Workers. Applying first regenerates the deployment configuration from the checked-in bases, including changes to bindings, variables or migrations. Append future Durable Object class migrations to Wrangler and SQL migrations to the application. Do not rename `TamsStore`, change `STORE_ID`, switch buckets or delete migration entries as an upgrade procedure. Existing AWS/PostgreSQL data migration is outside this installation's scope.
