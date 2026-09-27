# Changelog

## Unreleased

- Manage Node.js, uv and OpenTofu through Aqua for local development and CI; pin the tested Python interpreter through uv. Run the tool setup in the deployment guide before using the native commands.

- Move webhook configuration to the `webhook_allowed_hosts` OpenTofu input. Copy existing receiver entries into the installation's `terraform.tfvars` before applying; the new input and local Wrangler configuration default to an empty allowlist.

- Support `*.example.com` webhook allowlist entries for subdomains and log rejected callback hostnames without URL paths, query strings or credentials.

- Return the full extent of Segments overlapping a Flow timerange query, including collection members. Queries falling entirely in gaps now return an empty timerange.

- Replace `bucket_name` with the `storage_backends` OpenTofu map. State moves preserve the original `MEDIA` bucket, private domain and CORS resource. Keep existing storage identities when upgrading.
- Add selected-backend uploads, signed URLs and durable R2 copies through Object instance registration. Scope signing credentials to all configured buckets and retain the old token for the one-hour URL lifetime after rotation.

- Replace the static API token with OAuth client credentials and scoped JWT access tokens. Provision clients and update client configuration, then retain the inactive runtime `API_TOKEN` through OAuth validation and a complete hosted checker Pass. Only after both succeed, delete it with `npx wrangler secret delete API_TOKEN --config wrangler.deploy.json` and remove it from `.dev.vars`. Preserve `CLOUDFLARE_API_TOKEN` in `.env` for deployment. Reverting to static authentication also requires restoring its runtime secret.
- Add an independent auth Worker using Better Auth and OAuth Provider 1.7.6, JOSE 6.2.12 and native D1 storage. Apply `auth/migrations/0001_auth.sql` before deploying the issuer.
- Require `auth_issuer` and `tams_audience` installation inputs, a separate `BETTER_AUTH_SECRET`, and D1 permissions on the deployment credential.
- Add private token-verification RPC and API-wide read, write, delete and administrator permissions. Preserve existing Durable Object and R2 identities and data.
