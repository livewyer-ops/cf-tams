# Changelog

## Unreleased

Wrangler 4.168.0 requires Node.js 22.12 or later. Aqua supplies a compatible version.

The installer verifies and extracts a checksum-pinned TASM Cloudflare release. Free and Paid deploy the same prebuilt API Worker. Paid adds the packaged OAuth issuer and its D1 migrations.

Install the runtime before running OpenTofu. For an existing Wasm store, retain the Worker name, store ID, Durable Object class and migration chain, backend UUIDs and R2 bucket identities.

Set `wasm_anonymous_read` to allow public reads while retaining authentication for mutations and administrative scope checks. The Worker name, store ID and custom domains are configurable in OpenTofu.
