import { readFile } from 'node:fs/promises';
const { sha256 } = JSON.parse(await readFile(new URL('../runtime.lock.json', import.meta.url)));
if (!process.argv.includes('--config')) process.argv.push('--config', 'auth/wrangler.deploy.json');
await import(new URL(`../.runtime/${sha256}/auth/operator.mjs`, import.meta.url));
