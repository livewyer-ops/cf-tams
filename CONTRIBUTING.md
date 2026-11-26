# Contributing

Change OpenTofu resources, installer settings and operator guides here. Submit application, authentication and browser changes to [TASM](https://github.com/livewyer-ops/tasm).

## Run local checks

Install the pinned tools and runtime using the [deployment guide](docs/deployment.md#set-up-tools). Run these checks from the repository root.

```sh
npm test
tofu -chdir=infra init -backend=false -lockfile=readonly
tofu -chdir=infra fmt -check
tofu -chdir=infra validate
tofu -chdir=infra test
```

To test a candidate offline, set the repository, version, byte length and SHA-256 digest in `runtime.lock.json`, then run `npm run runtime:install -- /path/to/archive.tar.gz` in a clean checkout. Test both profiles with the exact archive that will be distributed. Keep release builds and credentials outside Git.

## Test a deployed installation

Use [Verify the API and media](docs/deployment.md#verify-the-api-and-media) on a disposable installation. Include the tested archive digest and profile when reporting results.
