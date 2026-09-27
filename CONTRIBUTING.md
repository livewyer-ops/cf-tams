# Contributing

Report bugs and propose changes through [GitHub Issues](https://github.com/livewyer-ops/cf-tams/issues). Include the version or commit, reproduction steps and expected behaviour. Use [SECURITY.md](SECURITY.md) for vulnerabilities.

## Set up a development checkout

Clone the repository and complete [tool setup](docs/deployment.md#set-up-tools). The [local tutorial](docs/first-store.md) creates an isolated store and OAuth client. Development uses the base Wrangler configurations; generated deployment configurations target provisioned Cloudflare resources.

The TAMS contract is pinned to [BBC TAMS 8.2](https://github.com/bbc/tams/tree/8.2). Check the OpenAPI specification, application notes and accepted architecture decisions when changing API behaviour. Preserve API identifiers and wire formats. Add a regression test for a behaviour change; do not weaken independent conformance tests to obtain a pass.

## Run local checks

From the repository root:

```sh
uv run --locked pytest -q
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
tofu -chdir=infra init -backend=false -input=false -lockfile=readonly
tofu -chdir=infra fmt -check
tofu -chdir=infra validate
tofu -chdir=infra test
npx --no-install wrangler deploy --dry-run
npm run auth:types
npm run auth:check
npx --no-install wrangler deploy --config auth/wrangler.jsonc --dry-run
npm run auth:test
```

The Python suite runs adapted TAMOSS conformance cases and SQLite, cleanup, webhook and signing checks. Botocore is a development dependency for comparison with AWS signatures; it is excluded from the Worker.

`npm run auth:test` starts both Workers locally, provisions clients and checks issuance, permissions, credential rotation, signing-key rotation, CORS and failure handling. It also runs the Workers media integration checks with an OAuth administrator token. The [CI workflow](.github/workflows/check.yml) runs these checks and checks for lockfile drift.

## Test a deployed installation

Use a disposable installation and a dedicated OAuth administrator client. The media tests create resources and upload test data. Supply `TAMS_TEST_URL` and `TAMS_TEST_TOKEN` through a protected environment file or secret manager, then run:

```sh
uv run --locked pytest -q tests/test_worker.py
```

Optional `TAMS_TEST_ORIGIN` enables the browser CORS check. The live URL-expiry check also requires `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`. Checks requiring unavailable credentials are skipped; report those skips with the results. Do not include access tokens or signed URLs in shared logs.

Locally, the tests upload 2 MB through Wrangler's R2 API and check registration, concurrent overlap rejection, encoded identifiers, transaction rollback and alarm-driven metadata deletion. A deployed installation exercises signed PUT/GET, conditional uploads, byte ranges and signature tampering. With two deployed backends, the suite also checks selected-backend uploads, byte-exact copies in both directions and last-instance deletion protection. Physical cleanup and retry recovery use aged allocations and injected failures in the SQLite tests.

Run the independent checker as described in [Qualify the API](docs/deployment.md#qualify-the-api). Keep the full report with both Worker versions and note failures, incomplete execution and residual resources. API qualification and successful playback cover different behaviours; test representative media with the intended client as well.

## Submit a change

Keep a pull request focused on one change and describe its effect on users. Include reproduction steps for a bug fix and the relevant check results, including anything not run. Update the affected documentation and put release-specific migration prerequisites in [CHANGELOG.md](CHANGELOG.md).

Keep secrets, local state, generated deployment configuration and test reports out of the pull request. Dependency changes must include their lockfiles; follow [Maintain dependencies](docs/deployment.md#maintain-dependencies).
