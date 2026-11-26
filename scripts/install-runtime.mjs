import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { mkdir, mkdtemp, open, readFile, rename, rm } from 'node:fs/promises';
import { join } from 'node:path';

const lock = JSON.parse(await readFile(new URL('../runtime.lock.json', import.meta.url), 'utf8'));
const root = new URL('../.runtime/', import.meta.url);
await mkdir(root, { recursive: true });
const staging = await mkdtemp(new URL('install-', root));
try {
  const asset = `tasm-cloudflare-${lock.version}.tar.gz`;
  const archive = join(staging, asset);
  let source;
  if (process.argv[2]) {
    source = createReadStream(process.argv[2]);
  } else {
    const url = `https://github.com/${lock.repository}/releases/download/v${lock.version}/${asset}`;
    const result = await fetch(url, { signal: AbortSignal.timeout(60_000) });
    if (!result.ok) throw new Error(`Download failed (HTTP ${result.status}). Check that the pinned release contains ${asset}.`);
    source = result.body;
  }
  const hash = createHash('sha256');
  const output = await open(archive, 'wx');
  let size = 0;
  try {
    for await (const chunk of source) {
      size += chunk.length;
      if (size > lock.bytes) throw new Error('Runtime exceeds the pinned archive size');
      hash.update(chunk);
      await output.writeFile(chunk);
    }
  } finally { await output.close(); }
  const digest = hash.digest('hex');
  if (size !== lock.bytes || digest !== lock.sha256) throw new Error('Runtime checksum does not match runtime.lock.json');
  const unpacked = join(staging, 'release');
  await mkdir(unpacked);
  execFileSync('tar', ['-xzf', archive, '-C', unpacked]);
  // Each digest owns an immutable directory. Concurrent installs can share the winner.
  try { await rename(unpacked, new URL(digest, root)); }
  catch (e) { if (!['EEXIST', 'ENOTEMPTY'].includes(e.code)) throw e; }
  console.log(`TASM ${lock.version} installed at .runtime/${digest}`);
} finally { await rm(staging, { recursive: true, force: true }); }
