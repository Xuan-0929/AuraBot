/** Launches the local Electron/Vite JS entry with an isolated temporary user-data root.
 * Uses Node directly on every OS, avoiding npm/npx shell shims; Main owns Backend lifecycle. */
import { mkdtempSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";

const mode = process.argv[2];

if (mode === "--help" || mode === "-h" || !["dev", "start"].includes(mode)) {
  console.log("Usage: node scripts/run-clean.mjs <dev|start>");
  console.log("Creates a temporary AURABOT_USER_DATA_DIR, enables AURABOT_DEBUG_PET=1, and skips system config.");
  process.exit(mode ? 0 : 1);
}

const userDataDir = mkdtempSync(join(tmpdir(), `aurabot-${mode}-clean-`));
const command = process.execPath;
const cli = path => fileURLToPath(new URL('../node_modules/' + path, import.meta.url));
const args =
  mode === "dev"
    ? [cli("electron-vite/bin/electron-vite.js"), "dev"]
    : [cli("electron/cli.js"), "out/main/index.js"];

console.log(`AuraBot clean ${mode} userData: ${userDataDir}`);

const child = spawn(command, args, {
  cwd: new URL('../', import.meta.url),
  env: {
    ...process.env,
    AURABOT_USER_DATA_DIR: userDataDir,
    AURABOT_DEBUG_PET: "1",
    AURABOT_DISABLE_SYSTEM_CONFIG: "1"
  },
  stdio: "inherit"
});
child.once('error', error => { console.error(error.message); process.exitCode = 1; });

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    child.kill(signal);
  });
}

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }

  process.exit(code ?? 0);
});
