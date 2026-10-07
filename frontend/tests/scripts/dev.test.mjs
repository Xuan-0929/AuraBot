/** Executes the real development launcher with local process doubles, proving argument validation
 * and the electron-vite/application argument boundary without starting Electron or rebuilding native code. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
const launcher=fileURLToPath(new URL('../../scripts/dev.mjs',import.meta.url));
import { spawnSync } from 'node:child_process';

test('forwards all four modes after the electron-vite argument separator', () => {
  const root = mkdtempSync(join(tmpdir(), 'aurabot-dev-test-'));
  try {
    for (const name of ['npm', 'npx']) writeFileSync(join(root, name),
      '#!/usr/bin/env node\nconsole.log(JSON.stringify({command:' + JSON.stringify(name) + ',args:process.argv.slice(2)}));\n', {mode:0o755});
    for (const flags of [[], ['--debug'], ['--lock-memory'], ['--debug', '--lock-memory']]) {
      const result = spawnSync(process.execPath, [launcher, ...flags], {encoding:'utf8', env:{...process.env, PATH:root + ':' + process.env.PATH}});
      assert.equal(result.status, 0, result.stderr);
      const calls = result.stdout.trim().split('\n').map(line => JSON.parse(line));
      assert.deepEqual(calls.at(-1), {command:'npx', args:['electron-vite', 'dev', '--', ...flags.map(flag=>flag==='--debug'?'--aurabot-debug':'--aurabot-lock-memory')]});
    }
    for (const flags of [['--help'], ['--lock-memroy']]) {
      const result = spawnSync(process.execPath, [launcher, ...flags], {encoding:'utf8', env:{...process.env, PATH:root + ':' + process.env.PATH}});
      assert.equal(result.status, flags[0] === '--help' ? 0 : 1);
      assert.ok(!result.stdout.includes('"command"'), 'help/errors must not launch children');
      assert.match(result.stdout + result.stderr, /--debug|--lock-memroy/);
    }
  } finally { rmSync(root, {recursive:true, force:true}); }
});
