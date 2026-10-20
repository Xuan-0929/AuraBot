/** Prepares Pi dependencies in a user-selected AuraBot installation directory from this source checkout.
 * Copies the pinned manifests and local extension, runs npm ci sequentially for both packages,
 * then prepares verified speech models/licenses using the same manifest as Electron.
 * npm runs inside each package: npm 12 --prefix misidentifies scoped package roots during ci.
 * Settings/Profile/history are untouched; launch Electron with AURABOT_USER_DATA_DIR set to this root.
 * This is a source-development installer, not an application packaging or distribution workflow.
 */
import { cpSync, mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { npmCommand } from './npm-command.mjs';
import { prepareSpeechAssets } from './prepare-speech-assets.mjs';

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

export async function installRuntime(root) {
    for (const directory of prepareRuntime(root)) {
      const [node, args] = npmCommand();
      const result = spawnSync(node, [...args, 'ci', '--no-audit', '--no-fund'], { cwd: directory, stdio: 'inherit', windowsHide: true });
      if (result.error || result.status !== 0) {
        throw new Error(result.error?.message ?? 'Pi dependency installation failed.');
      }
    }
    await prepareSpeechAssets(resolve(root));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    if (process.argv.length !== 3) throw new Error('Usage: node frontend/scripts/install-runtime.mjs <installation-directory>');
    await installRuntime(process.argv[2]);
    console.log(`Pi 0.84.2, AuraBot extension and speech assets ready. Set AURABOT_USER_DATA_DIR to ${resolve(process.argv[2])} when starting AuraBot.`);
  } catch(error) { console.error(error.message); process.exitCode=1; }
}
