# TASM on Cloudflare

Deploy the BBC TAMS 8.2 API to your Cloudflare account with OpenTofu and Wrangler. One SQLite Durable Object stores metadata. Media clients upload and read bytes directly in private R2 buckets using signed URLs.

| Deployment profile | API access | Cloudflare resources |
| --- | --- | --- |
| Free | Shared HTTP Basic credentials | API Worker, Durable Object and R2 |
| Paid | Scoped OAuth client credentials | Same API Worker and storage, plus an issuer Worker and D1 |

Start with [your first local store](docs/first-store.md) or [install on Cloudflare](docs/deployment.md#install). The installer verifies and unpacks the TASM release pinned in [runtime.lock.json](runtime.lock.json). Installing requires Node.js, OpenTofu, Wrangler and tar.

TASM maintains the Rust application, both Wasm builds, and the browser Demo and Playpen. This repository owns Cloudflare installation settings and provisioning. [Application source](https://github.com/livewyer-ops/tasm) · [Deployment pattern](https://tamoss.com/patterns/tasm-cloudflare/) · [Operator documentation](docs/README.md)

Licensed under [Apache-2.0](LICENSE). The runtime distribution includes its own licence and third-party notices.
