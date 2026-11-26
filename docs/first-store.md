# Your first local store

On Linux or macOS, use Node.js with npm, tar and curl 7.82 or later. The [tool manifest](../aqua.yaml) pins the Node.js version. Wrangler runs the database and R2 storage on your computer. You can follow this tutorial without a Cloudflare account.

From the repository root, install the dependencies and start the store.

```sh
npm ci
npm run dev
```

The first start downloads and verifies the pinned TASM release. When Wrangler is ready, open a second terminal in the same checkout and read the service description using the generated local credentials.

```sh
curl --fail --netrc-file .local/first-store/netrc http://127.0.0.1:8787/service
```

Look for `"api_version": "8.2"` in the response.

## Create and read a Flow

Use this example data Flow to try a write. The Flow ID in the URL matches the `id` in the request body.

```sh
flow=http://127.0.0.1:8787/flows/00000000-0000-4000-8000-000000000101
curl --fail --netrc-file .local/first-store/netrc --request PUT "$flow" \
  --json '{
    "id": "00000000-0000-4000-8000-000000000101",
    "source_id": "00000000-0000-4000-8000-000000000102",
    "format": "urn:x-nmos:format:data",
    "codec": "application/json",
    "essence_parameters": {}
  }'
curl --fail --netrc-file .local/first-store/netrc "$flow"
```

The final request returns the Flow with the same `id`. Press Ctrl-C in the first terminal to stop the Worker. Run `npm run dev` again and repeat the final request to read the saved Flow.

To try signed media transfers or OAuth, [deploy to Cloudflare](deployment.md#install).
