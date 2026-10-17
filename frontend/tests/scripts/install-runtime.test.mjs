/** Checks source installer staging without downloads: package links stay within the selected root,
 * and repeated staging preserves user settings/Profile files. npm itself owns dependency installation.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, existsSync, writeFileSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { prepareRuntime } from '../../scripts/install-runtime.mjs';
import { npmCommand } from '../../scripts/npm-command.mjs';
import { spawnSync } from 'node:child_process';

test('runs npm as JavaScript without a shell, including Windows layouts and metacharacter paths', () => {
  const root=mkdtempSync(join(tmpdir(),'aura npm 中文 & '));
  try {
    const cli=join(root,'node_modules/npm/bin/npm-cli.js');mkdirSync(dirname(cli),{recursive:true});
    writeFileSync(cli,'console.log(JSON.stringify(process.argv.slice(2)))');
    const [node,args]=npmCommand({npm_execpath:cli},process.execPath);
    const forwarded=['ci','--prefix',join(root,'data & more')];
    const result=spawnSync(node,[...args,...forwarded],{encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);assert.deepEqual(JSON.parse(result.stdout),forwarded);
    assert.deepEqual(npmCommand({Path:root},join(root,'node.exe')),[join(root,'node.exe'),[cli]]);
  } finally {rmSync(root,{recursive:true,force:true});}
});

test('stages the pinned runtime and local extension in a source-independent installation', () => {
  const root = mkdtempSync(join(tmpdir(), 'aura-pi-install-'));
  try {
    writeFileSync(join(root, 'settings.json'), 'existing-settings');
    const packages = prepareRuntime(root);
    assert.deepEqual(packages, [join(root,'pi-aurabot-task'),join(root,'pi-runtime')]);
    const manifest = JSON.parse(readFileSync(join(root,'pi-runtime/package.json'),'utf8'));
    assert.equal(manifest.dependencies['@scpz24/pi-aurabot-task'], 'file:../pi-aurabot-task');
    assert.ok(existsSync(join(root,'pi-aurabot-task/aurabot-task/index.ts')));
    assert.ok(existsSync(join(root,'pi-aurabot-task/prompts/task-system.md')));
    assert.ok(existsSync(join(root,'pi-runtime/package-lock.json')));
    prepareRuntime(root);
    assert.equal(readFileSync(join(root,'settings.json'),'utf8'),'existing-settings');
  } finally { rmSync(root,{recursive:true,force:true}); }
});
