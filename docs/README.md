# Documentation

## Tutorial

[Your first local store](first-store.md) takes you through starting the Workers, obtaining an OAuth token and creating a Flow. It needs no Cloudflare account.

## How-to guides

- [Deploy and operate](deployment.md): [install](deployment.md#install), [add storage](deployment.md#add-a-storage-backend), [configure browser access](deployment.md#configure-browser-access), [enable webhooks](deployment.md#enable-webhooks), [qualify](deployment.md#qualify-the-api), [update and recover](deployment.md#update-and-recover), or [decommission](deployment.md#decommission-an-installation).
- [Configure OAuth clients](oauth.md): provision clients, connect [TAMSTool](oauth.md#tamstool) or [Cutting Room](oauth.md#cutting-room), and rotate or disable credentials.
- [Troubleshooting](troubleshooting.md): investigate failed requests and playback problems.
- [Contributing](../CONTRIBUTING.md): set up a development checkout and run checks.

## Reference

- [Configuration and API reference](reference.md): configuration ownership, defaults, endpoints, permissions and limits.
- [BBC TAMS 8.2 OpenAPI specification](https://github.com/bbc/tams/blob/8.2/api/TimeAddressableMediaStore.yaml): the API contract.
- [Changelog](../CHANGELOG.md): release changes and migration prerequisites.

## Explanation

[Architecture](design.md) explains the storage and authentication design. The upstream [BBC TAMS documentation](https://github.com/bbc/tams/blob/8.2/docs/README.md), [application notes](https://github.com/bbc/tams/tree/8.2/docs/appnotes) and [architecture decisions](https://github.com/bbc/tams/tree/8.2/docs/adr) describe the TAMS model and its design choices.
