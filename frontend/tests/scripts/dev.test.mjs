/** Executes the real development launcher with local process doubles, proving argument validation
 * and the electron-vite/application argument boundary without starting Electron or rebuilding native code. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync, mkdirSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const sourceLauncher=fileURLToPath(new URL('../../scripts/dev.mjs',import.meta.url));
import { spawnSync } from 'node:child_process';

test('forwards all four modes after the electron-vite argument separator', () => {
  const root = mkdtempSync(join(tmpdir(), 'aurabot dev 中文 & test-'));
  try {
    const launcher=join(root,'scripts/dev.mjs');mkdirSync(dirname(launcher),{recursive:true});copyFileSync(sourceLauncher,launcher);
    for (const [name,relative] of [['rebuild','@electron/rebuild/lib/cli.js'], ['vite','electron-vite/bin/electron-vite.js']]) {
      const cli=join(root,'node_modules',relative);mkdirSync(dirname(cli),{recursive:true});
      writeFileSync(cli,'console.log(JSON.stringify({command:' + JSON.stringify(name) + ',args:process.argv.slice(2)}));\n');
    }
    for (const flags of [[], ['--debug'], ['--lock-memory'], ['--debug', '--lock-memory']]) {
      const result = spawnSync(process.execPath, [launcher, ...flags], {encoding:'utf8'});
      assert.equal(result.status, 0, result.stderr);
      const calls = result.stdout.trim().split('\n').map(line => JSON.parse(line));
      assert.deepEqual(calls[0], {command:'rebuild',args:['-f','-w','better-sqlite3']});
      assert.deepEqual(calls.at(-1), {command:'vite', args:['dev', '--', ...flags.map(flag=>flag==='--debug'?'--aurabot-debug':'--aurabot-lock-memory')]});
    }
    for (const flags of [['--help'], ['--lock-memroy']]) {
      const result = spawnSync(process.execPath, [launcher, ...flags], {encoding:'utf8'});
      assert.equal(result.status, flags[0] === '--help' ? 0 : 1);
      assert.ok(!result.stdout.includes('"command"'), 'help/errors must not launch children');
      assert.match(result.stdout + result.stderr, /--debug|--lock-memroy/);
    }
  } finally { rmSync(root, {recursive:true, force:true}); }
});
