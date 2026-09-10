/** Prepares Pi dependencies in a user-selected AuraBot installation directory from this source checkout.
 * Copies the pinned manifests and local extension, then runs npm ci sequentially for both packages.
 * Settings/Profile/history are untouched; launch Electron with AURABOT_USER_DATA_DIR set to this root.
 * This is a source-development installer, not an application packaging or distribution workflow.
 */
import { cpSync, mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

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

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  if (process.argv.length !== 3) {
    console.error('Usage: node frontend/scripts/install-runtime.mjs <installation-directory>');
    process.exitCode = 1;
  } else {
    for (const directory of prepareRuntime(process.argv[2])) {
      const result = spawnSync('npm', ['ci', '--prefix', directory, '--no-audit', '--no-fund'], { stdio: 'inherit' });
      if (result.error || result.status !== 0) {
        console.error(result.error?.message ?? 'Pi dependency installation failed.');
        process.exitCode = result.status || 1;
        break;
      }
    }
    if (!process.exitCode) console.log(`Pi runtime installed. Set AURABOT_USER_DATA_DIR to ${resolve(process.argv[2])} when starting AuraBot.`);
  }
}
