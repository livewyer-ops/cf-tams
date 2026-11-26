# TASM on Cloudflare

Deploy the [BBC TAMS 8.2 API](https://github.com/bbc/tams/tree/8.2) to your Cloudflare account with OpenTofu and Wrangler. One SQLite Durable Object stores metadata. Media clients upload and read bytes directly in private R2 buckets using signed URLs.

| Deployment profile | API access | Cloudflare resources |
| --- | --- | --- |
| Free | Shared HTTP Basic credentials | API Worker, Durable Object and R2 |
| Paid | Scoped OAuth client credentials | Same API Worker and storage, plus an issuer Worker and D1 |

- [Try locally](docs/first-store.md) to run the API on your computer and create a Flow without a Cloudflare account.
- [Deploy to Cloudflare](docs/deployment.md#install) to install the pinned TASM release in your account.

For a browser-local store or the Demo and Playpen, see TASM. TASM supplies the application archive. This installer provisions the Cloudflare resources. [Application source](https://github.com/livewyer-ops/tasm) · [Deployment pattern](https://tamoss.com/patterns/tasm-cloudflare/) · [Operator documentation](docs/README.md)

[Troubleshooting](docs/troubleshooting.md) · [Contributing](CONTRIBUTING.md) · [Private security reporting](SECURITY.md)

Licensed under [Apache-2.0](LICENSE). The runtime distribution includes its own licence and third-party notices.
