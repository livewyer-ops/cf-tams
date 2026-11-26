import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cp, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { promisify } from 'node:util';

const run = promisify(execFile);

test('only a verified runtime becomes visible, including on concurrent installs', async t => {
  const root = await mkdtemp(join(tmpdir(), 'tasm-installer-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'scripts'));
  await cp(new URL('../scripts/install-runtime.mjs', import.meta.url), join(root, 'scripts/install-runtime.mjs'));
  await writeFile(join(root, 'worker.mjs'), 'export default {};');
  await run('tar', ['-czf', 'runtime.tar.gz', 'worker.mjs'], { cwd: root });
  const archive = await readFile(join(root, 'runtime.tar.gz'));
  const sha256 = createHash('sha256').update(archive).digest('hex');
  await writeFile(join(root, 'runtime.lock.json'), JSON.stringify({ version: '0.1.0', bytes: archive.length, sha256 }));
  const install = path => run(process.execPath, ['scripts/install-runtime.mjs', path], { cwd: root });

  for (const bytes of [archive.subarray(1), Buffer.alloc(archive.length), Buffer.concat([archive, Buffer.from('x')])]) {
    await writeFile(join(root, 'invalid.tar.gz'), bytes);
    await assert.rejects(install('invalid.tar.gz'), /checksum does not match|exceeds the pinned archive size/);
    assert.deepEqual(await readdir(join(root, '.runtime')), []);
  }
  await Promise.all([install('runtime.tar.gz'), install('runtime.tar.gz')]);
  assert.deepEqual(await readdir(join(root, '.runtime')), [sha256]);
  assert.equal(await readFile(join(root, '.runtime', sha256, 'worker.mjs'), 'utf8'), 'export default {};');
});
