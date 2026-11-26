# Your first local store

Use Wrangler's local Durable Object and R2 emulation to start a Free-profile store. In a new installer checkout, follow [Set up tools](deployment.md#set-up-tools) to install the pinned runtime. From the repository root, copy its API modules into a local development directory.

```sh
digest=$(node -p 'require("./runtime.lock.json").sha256')
mkdir -p .local
cp -R ".runtime/$digest/api" .local/first-store
node --input-type=module <<'JS'
import { writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
const password = randomBytes(32).toString('hex');
writeFileSync('.local/first-store/.dev.vars', [
  'BASIC_AUTH_USERNAME=operator', 'BASIC_AUTH_PASSWORD=' + password,
  'R2_ACCESS_KEY_ID=local', 'R2_SECRET_ACCESS_KEY=local',
].join('\n') + '\n', { flag: 'wx', mode: 0o600 });
JS
npx wrangler dev --local --config .local/first-store/wrangler.json --port 8787
```

In another terminal in the same checkout, read the service description and create an empty data Flow.

```sh
. ./.local/first-store/.dev.vars
api=http://127.0.0.1:8787
flow=$(node -p 'crypto.randomUUID()')
source=$(node -p 'crypto.randomUUID()')
curl --fail --user "$BASIC_AUTH_USERNAME:$BASIC_AUTH_PASSWORD" "$api/service"
curl --fail --user "$BASIC_AUTH_USERNAME:$BASIC_AUTH_PASSWORD" \
  -X PUT -H 'Content-Type: application/json' \
  -d "{\"id\":\"$flow\",\"source_id\":\"$source\",\"format\":\"urn:x-nmos:format:data\",\"codec\":\"application/json\",\"essence_parameters\":{}}" \
  "$api/flows/$flow"
curl --fail --user "$BASIC_AUTH_USERNAME:$BASIC_AUTH_PASSWORD" "$api/flows/$flow"
```

The service response includes `api_version` set to `8.2`. The final GET returns the Flow you created with its `id` equal to `$flow`.

Stop the local Worker with Ctrl-C. Restart it with the same Wrangler configuration to reopen the store. For signed media transfers and OAuth provisioning, follow [Deploy and operate](deployment.md#install).
