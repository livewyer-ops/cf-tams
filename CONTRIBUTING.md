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

Run the independent checker on a disposable installation and verify signed R2 upload, time-range lookup and ranged retrieval. Check that a populated Wasm installation reopens with the same metadata and media after redeployment. Record the tested archive digest, profile and checker scope. Follow [Qualify the API](docs/deployment.md#qualify-the-api).
