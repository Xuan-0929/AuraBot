/** Resolves npm's JavaScript CLI for source-runtime installation on macOS and Windows.
 * Invoked by install-runtime, it honors npm's own CLI identity or locates a standard Node/npm
 * installation. Node executes the CLI directly so paths and arguments never enter a command shell. */
import { existsSync, realpathSync } from 'node:fs';
import { delimiter, dirname, join } from 'node:path';

export function npmCommand(env = process.env, node = process.execPath) {
  const candidates = [env.npm_execpath];
  const path = Object.entries(env).find(([key]) => key.toLowerCase() === 'path')?.[1] ?? '';
  for (const directory of [dirname(node), ...path.split(delimiter).filter(Boolean)]) {
    const npm = join(directory, 'npm');
    if (existsSync(npm)) candidates.push(realpathSync(npm));
    candidates.push(join(directory, 'node_modules/npm/bin/npm-cli.js'), join(directory, '../lib/node_modules/npm/bin/npm-cli.js'));
  }
  const cli = candidates.find(candidate => candidate?.endsWith('npm-cli.js') && existsSync(candidate));
  if (!cli) throw new Error('npm CLI not found. Install Node.js with npm, then retry.');
  return [node, [cli]];
}
