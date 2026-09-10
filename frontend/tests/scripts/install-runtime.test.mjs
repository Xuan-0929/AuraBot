/** Checks source installer staging without downloads: package links stay within the selected root,
 * and repeated staging preserves user settings/Profile files. npm itself owns dependency installation.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, existsSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { prepareRuntime } from '../../scripts/install-runtime.mjs';

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
