# Cloudflare TAMS

cf-tams is an implementation of [BBC TAMS 8.2](https://github.com/bbc/tams/tree/8.2) on Cloudflare Workers. A Time Addressable Media Store records media as timestamped Segments in Flows, so clients can find and retrieve a time range without downloading a whole recording.

The API runs in a Python Worker with FastAPI. A SQLite-backed Durable Object holds metadata, private R2 buckets hold media, and a separate TypeScript Worker with D1 provides OAuth client credentials.

## Features

- Sources, Flows, Segments, collections, Profiles and tags through the TAMS API.
- Direct media upload and download with signed R2 URLs and HTTP Range support.
- Multiple R2 storage backends, with asynchronous Object copying and deletion.
- Scoped OAuth access, configurable browser CORS and webhook receiver allowlists.
- Native OpenTofu infrastructure configuration and Wrangler deployments.

Clients provide ingest and playback. cf-tams has no built-in media browser, transcoder or OAuth login UI. See the [supported scope and operating limits](docs/reference.md#scope-and-limits) before sizing an installation.

## Get started

Start with [your first local store](docs/first-store.md). The tutorial runs both Workers, obtains an access token and creates a Flow without a Cloudflare account.

You need Git, a POSIX shell and [Aqua](https://aquaproj.github.io/docs/install/) on Linux or macOS. The repository supplies pinned Node.js, Python, uv, OpenTofu and Wrangler versions through its tool manifest and lockfiles.

For real media transfers, follow [Deploy and operate](docs/deployment.md#install). Deployment requires a Cloudflare account with Workers Paid and R2 enabled. OpenTofu provisions storage and generates configuration; Wrangler applies migrations and deploys the Workers and their secrets.

## Documentation

| Guide | Use it to |
| --- | --- |
| [Documentation index](docs/README.md) | Find tutorials, how-to guides, reference and explanation. |
| [Your first local store](docs/first-store.md) | Try the API and OAuth locally. |
| [Deploy and operate](docs/deployment.md) | Install, configure, update and remove an installation. |
| [Configure OAuth clients](docs/oauth.md) | Provision access for TAMSTool, Cutting Room and other clients. |
| [Configuration and API reference](docs/reference.md) | Look up settings, endpoints, permissions and limits. |
| [Troubleshooting](docs/troubleshooting.md) | Diagnose authentication, webhooks, CORS and playback. |
| [Architecture](docs/design.md) | Understand metadata consistency, authentication and media storage. |

The API contract is pinned to [BBC TAMS 8.2's OpenAPI specification](https://github.com/bbc/tams/blob/8.2/api/TimeAddressableMediaStore.yaml). An installation's qualification result applies to the tested deployment and the checker's published scope; see [Qualify the API](docs/deployment.md#qualify-the-api).

## Contribute and get support

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, checks and pull requests. Report bugs and request features through [GitHub Issues](https://github.com/livewyer-ops/cf-tams/issues). Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Licence

Licensed under the [Apache License 2.0](LICENSE). The API and domain rules derive from [TAMOSS](https://github.com/livewyer-ops/tamoss); [NOTICE](NOTICE) records the upstream revisions and BBC TAMS attribution.
