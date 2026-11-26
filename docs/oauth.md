# Configure OAuth clients

The Paid profile accepts JWT bearer tokens obtained with the OAuth `client_credentials` grant. For the Free profile, use [Basic authentication](deployment.md#configure-basic-authentication). Client administration uses Better Auth's server APIs through Wrangler's authenticated D1 binding. There is no public operator login or OAuth administration UI. See the [endpoint reference](reference.md#endpoints) for token issuance and discovery URLs.

## Provision clients

Complete [installation](deployment.md#install), including the D1 migration. Create an ignored `auth/.env.operator` file containing `AUTH_OWNER_PASSWORD`, a separate randomly generated password of at least 32 characters. Protect it with mode 0600 and retain a secure backup. Load it with the Cloudflare deployment credential:

```sh
set -a
. ./.env
. ./auth/.env.operator
set +a
npm run auth:client -- bootstrap --remote
```

Bootstrap creates the private operator account and TAMS resource. Run it once per database. The CLI uses `auth/wrangler.deploy.json` by default. For a local issuer, omit `--remote` and pass `--config` with the path to its local Wrangler configuration.

Create an independent client for each application or operator. For example, to grant read access:

```sh
npm run auth:client -- create --remote \
  --name reader --scopes tams-api/read --output auth/.reader-credentials.json
```

Use an ignored destination for credentials. The CLI creates the output file with mode 0600 and refuses to overwrite it. It does not print the secret. Transfer credentials through a secure channel to the application's operator.

The default authentication method is `client_secret_basic`. Select `--method client_secret_post` when the application sends credentials in the form body. A client must use its registered method; provision a new client if that method needs to change.

## Connect TAMSTool or CuttingRoom

Configure the application's OAuth client credentials option with the TAMS API URL and the `token_url`, `client_id` and `client_secret` from its protected credential file. A request without `scope` uses the client's authorised default scopes; a request without `resource` targets this installation's TAMS API.

### TAMSTool

Create a read-only playback client using Basic authentication:

```sh
npm run auth:client -- create --remote \
  --name tamstool --scopes tams-api/read \
  --output auth/.tamstool-credentials.json
```

In TAMSTool, select **Client / Secret**, enter those credentials and the store URL, test the connection and save the store. Configure the browser origin in both `api_cors.origins` and `media_origins`; for the hosted TAMSTool page, it is `https://cuttingroom.github.io`. Follow [Configure browser access](deployment.md#configure-browser-access), including applying OpenTofu and redeploying both Workers.

### Cutting Room

Create a client using credentials in the form body, with read and write access:

```sh
npm run auth:client -- create --remote \
  --name cuttingroom --method client_secret_post \
  --scopes tams-api/read,tams-api/write --output auth/.cuttingroom-credentials.json
```

Enter the store URL, token endpoint, client ID and secret in Cutting Room's TAMS store configuration. Read and write cover browsing and webhook registration. Add `tams-api/delete` only if the workflow needs Flow or Segment deletion.

Permit the application's browser origin through [CORS](deployment.md#configure-browser-access) and its callback receiver hostname through the [webhook allowlist](deployment.md#enable-webhooks). The receiver may differ from the browser origin. Confirm the receiver hostname in the application's configuration or the TAMS Worker's rejection log, then apply OpenTofu and redeploy the affected Workers.

### Browser credentials and renewal

Enter credentials through the application's configuration; do not embed shared secrets in a public build. Browser-held secrets are readable by that browser's operator, so grant only the access required. R2 signed requests use their own signature and must not receive an OAuth Authorization header.

Access tokens expire after 15 minutes. Clients obtain another token with the same grant; refresh tokens are not issued. Applications without automatic renewal need to reconnect after expiry. Existing signed media URLs retain their own expiry.

## Permissions

Use the [OAuth permission reference](reference.md#oauth-permissions) to select scopes. Combine them with a comma-separated `--scopes` value; write and delete do not imply read. After changing scopes, obtain a new token before testing access.

## Rotate or disable credentials

Load the deployment credential and operator password as in [Provision clients](#provision-clients). List clients to find the ID, then choose the relevant command below:

```sh
npm run auth:client -- list --remote
```

Rotate a secret, saving the replacement to a new protected file:

```sh
npm run auth:client -- rotate --remote \
  --client CLIENT_ID --output auth/.rotated-credentials.json
```

Change the allowed scopes:

```sh
npm run auth:client -- scopes --remote \
  --client CLIENT_ID --scopes tams-api/read,tams-api/write
```

Disable new token issuance:

```sh
npm run auth:client -- disable --remote \
  --client CLIENT_ID
```

Rotation invalidates the previous client secret immediately. Disabling removes the client's machine scope allowance. Already-issued JWTs remain usable until expiry, with up to 30 seconds of clock tolerance; scope reductions have the same delay. R2 URLs already issued remain usable for their original one-hour lifetime.

Restore a disabled client by assigning scopes again. If a client secret is lost, rotate it. Recover the operator password and `BETTER_AUTH_SECRET` from protected backups; arbitrarily replacing the latter prevents decryption of stored signing keys.

## Apply issuer migrations

The TASM release supplies the D1 migrations. Apply them using the generated issuer configuration before deploying the new issuer. Preserve the database, issuer URL and signing-key encryption secret across deployments. See [Install](deployment.md#install).
