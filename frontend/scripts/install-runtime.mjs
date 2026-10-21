/** Prepares Pi dependencies in a user-selected AuraBot installation directory from this source checkout.
 * Copies the pinned manifests and local extension, runs npm ci sequentially for both packages,
 * then checks the runtime through Backend's offline validator and prepares verified speech assets.
 * --check only validates; --runtime-only skips speech downloads so either stage can be retried.
 * npm runs inside each package: npm 12 --prefix misidentifies scoped package roots during ci.
 * Settings/Profile/history are untouched; launch Electron with AURABOT_USER_DATA_DIR set to this root.
 * This is a source-development installer, not an application packaging or distribution workflow.
 */
import { cpSync, mkdirSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { npmCommand } from './npm-command.mjs';
import { prepareSpeechAssets } from './prepare-speech-assets.mjs';
import { applicationDataRoot } from '../src/main/applicationPaths.ts';

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
export function prepareRuntime(installationDirectory) {
  const root = resolve(installationDirectory);
  const packages = {
    'pi-aurabot-task': ['package.json', 'package-lock.json', 'aurabot-task', 'prompts'],
    'pi-runtime': ['package.json', 'package-lock.json']
  };
  for (const [name, files] of Object.entries(packages)) {
    const destination = join(root, name);
    mkdirSync(destination, { recursive: true });
    if (destination === join(sourceRoot, name)) continue;
    for (const file of files) cpSync(join(sourceRoot, name, file), join(destination, file), { recursive: true });
  }
  return Object.keys(packages).map(name => join(root, name));
}

export function checkRuntime(root) {
  const python = join(sourceRoot, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(python)) throw new Error(`Python environment missing: ${python}\nRun: uv sync --project backend --frozen --extra speech --python 3.12`);
  const result = spawnSync(python, ['-m', 'backend.task.check_runtime', '--data-dir', resolve(root), '--node', process.execPath],
    { cwd: sourceRoot, stdio: ['ignore', 'inherit', 'inherit'], windowsHide: true });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? 'Runtime check failed. See the paths and reason above; repair this data directory before launch.');
}

export async function installRuntime(root, {runtimeOnly = false} = {}) {
    for (const directory of prepareRuntime(root)) {
      const [node, args] = npmCommand();
      const result = spawnSync(node, [...args, 'ci', '--no-audit', '--no-fund'], { cwd: directory, stdio: 'inherit', windowsHide: true });
      if (result.error || result.status !== 0) {
        throw new Error(result.error?.message ?? 'Pi dependency installation failed.');
      }
    }
    checkRuntime(root);
    if (!runtimeOnly) await prepareSpeechAssets(resolve(root));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const {values, positionals} = parseArgs({options:{check:{type:'boolean'}, 'runtime-only':{type:'boolean'}, help:{type:'boolean',short:'h'}}, allowPositionals:true});
    if (positionals.length > 1 || (values.check && values['runtime-only'])) throw new Error('Choose either --check or --runtime-only and at most one data directory.');
    if (values.help) {
      console.log('Usage: node frontend/scripts/install-runtime.mjs [installation-directory] [--check | --runtime-only]\nDefaults to the same data directory as source setup and Electron. Requires the prepared backend/.venv.');
    } else {
      const root = resolve(positionals[0] ?? applicationDataRoot());
      if (values.check) checkRuntime(root);
      else await installRuntime(root, {runtimeOnly:values['runtime-only']});
      console.log(`Runtime ${values.check ? 'checked' : 'ready'}. Data: ${root}${values.check || values['runtime-only'] ? '' : '\nSpeech assets ready.'}`);
    }
  } catch(error) { console.error(error.message); process.exitCode=1; }
}
