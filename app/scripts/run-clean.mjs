import { mkdtempSync } from "node:fs";
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
const command = "corepack";
const args =
  mode === "dev"
    ? ["pnpm", "dev"]
    : ["pnpm", "exec", "electron", "desktop/out/main/index.js"];

console.log(`AuraBot clean ${mode} userData: ${userDataDir}`);

const child = spawn(command, args, {
  env: {
    ...process.env,
    AURABOT_USER_DATA_DIR: userDataDir,
    AURABOT_DEBUG_PET: "1",
    AURABOT_DISABLE_SYSTEM_CONFIG: "1"
  },
  stdio: "inherit"
});

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
