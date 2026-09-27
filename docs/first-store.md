# Your first local store

Run cf-tams locally, obtain an OAuth access token and create, read and delete a Flow. The Workers use local D1, Durable Object and R2 emulation, so this tutorial needs no Cloudflare account or cloud credentials.

Use a new checkout with Git, a POSIX shell and [Aqua](https://aquaproj.github.io/docs/install/) installed. Add Aqua's binary directory to `PATH` as described by its installer. Keep ports 8787 and 8788 free.

## Prepare the checkout

```sh
git clone https://github.com/livewyer-ops/cf-tams.git cf-tams-local
cd cf-tams-local
aqua install
npm ci
uv sync --locked
```

Aqua installs the pinned tools, npm installs Wrangler, and uv installs Python and the development dependencies. On a glibc Linux system with musl also installed, use the [tool setup guidance](deployment.md#set-up-tools) if Node.js fails to start.

Create local secrets with the following command. It refuses to replace existing files and creates them with owner-only permissions. The R2 values are dummy credentials; the auth secret and operator password are generated for this checkout.

```sh
uv run --locked python - <<'PY'
import os
import secrets
from pathlib import Path

files = {
    Path('.dev.vars'): 'R2_ACCESS_KEY_ID=local-access-key\nR2_SECRET_ACCESS_KEY=local-secret-key\n',
    Path('auth/.dev.vars'): f'BETTER_AUTH_SECRET={secrets.token_hex(32)}\n',
    Path('auth/.env.operator'): f'AUTH_OWNER_PASSWORD={secrets.token_hex(32)}\n',
}
if any(path.exists() for path in files):
    raise SystemExit('Secret files already exist. Use a fresh checkout for this tutorial.')
for path, content in files.items():
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
        file.write(content)
print('Local secret files created.')
PY
```

The files are ignored by Git. Keep them between restarts: changing `BETTER_AUTH_SECRET` prevents decryption of existing signing keys.

## Create a local OAuth client

Apply the auth schema, create the operator account, then give the tutorial client permission to read, write and delete its Flow:

```sh
set -a
. ./auth/.env.operator
set +a
npx --no-install wrangler d1 migrations apply DB --local --config auth/wrangler.jsonc
npm run auth:client -- bootstrap
npm run auth:client -- create --name tutorial \
  --scopes tams-api/read,tams-api/write,tams-api/delete \
  --output auth/.tutorial-credentials.json
unset AUTH_OWNER_PASSWORD
```

Wrangler reports successful migrations. Bootstrap prints `Operator and TAMS resource created.` and client creation reports the credential file path. That file has mode 0600 and must not already exist. Run bootstrap only once for this local database.

## Start the Workers

Open a second terminal in `cf-tams-local` and start the OAuth Worker:

```sh
npx --no-install wrangler dev --config auth/wrangler.jsonc --ip 127.0.0.1 --port 8788
```

Open a third terminal in the same directory and start the API Worker:

```sh
npx --no-install wrangler dev --config wrangler.jsonc --ip 127.0.0.1 --port 8787
```

Wait for both Workers to report that they are ready. The API's `AUTH` service binding connects to the OAuth Worker. Leave both terminals running and return to the first terminal.

## Create and read a Flow

A Flow describes media with a common format and timeline. This example creates an empty data Flow with a generated Source ID. It does not upload media.

Run the following command. It reads the client credentials from the protected file, keeps the access token in memory and prints only request statuses. The final requests delete the Flow and confirm it is gone.

```sh
uv run --locked python - <<'PY'
import base64
import json
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

client = json.loads(Path('auth/.tutorial-credentials.json').read_text())
basic = base64.b64encode(f"{client['client_id']}:{client['client_secret']}".encode()).decode()
request = Request(client['token_url'], data=urlencode({'grant_type': 'client_credentials'}).encode(), headers={
    'Authorization': f'Basic {basic}',
    'Content-Type': 'application/x-www-form-urlencoded',
})
with urlopen(request, timeout=10) as response:
    token = json.load(response)['access_token']
    print(f'POST token: {response.status}')

def api(method, path, body=None):
    request = Request(client['api_url'] + path, method=method, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }, data=None if body is None else json.dumps(body).encode())
    with urlopen(request, timeout=10) as response:
        data = response.read()
        return response.status, json.loads(data) if data else None

status, service = api('GET', '/service')
assert status == 200
print(f'GET /service: {status}')
flow_id = str(uuid.uuid4())
path = f'/flows/{flow_id}'
status, flow = api('PUT', path, {
    'id': flow_id,
    'source_id': str(uuid.uuid4()),
    'label': 'My first local Flow',
    'format': 'urn:x-nmos:format:data',
    'codec': 'application/json',
    'essence_parameters': {},
})
assert status == 201
print(f'PUT Flow: {status}')
status, flow = api('GET', path)
assert status == 200 and flow['label'] == 'My first local Flow'
print(f'GET Flow: {status}')
status, deletion = api('DELETE', path)
assert status == 204
print(f'DELETE Flow: {status}')
try:
    api('GET', path)
except HTTPError as error:
    if error.code != 404:
        raise
    print('GET deleted Flow: 404')
else:
    raise SystemExit('The Flow still exists after deletion.')
PY
```

Expected output:

```text
POST token: 200
GET /service: 200
PUT Flow: 201
GET Flow: 200
DELETE Flow: 204
GET deleted Flow: 404
```

The empty Flow is deleted immediately with a `204` response; the final `404` confirms it is gone. Flows containing Segments can return `202` while deletion continues asynchronously.

## Stop and continue

Press Ctrl-C in each Worker terminal. Local state remains in `.wrangler/state` and `auth/.wrangler/state`, so you can restart the Workers and repeat the API example without bootstrapping again.

For uploads and playback, [deploy on Cloudflare](deployment.md#install) and follow [Use media URLs](deployment.md#use-media-urls). Local R2 emulation has no S3 presigning endpoint, so this tutorial does not exercise signed media transfers. Use a separate deployment checkout with real credentials.
