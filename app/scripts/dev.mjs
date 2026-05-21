import { spawn } from "node:child_process";

const processes = [
  {
    name: "backend",
    command: "python",
    args: ["-m", "uvicorn", "app.gateway.main:app", "--host", "127.0.0.1", "--port", "6401"],
    cwd: new URL("../backend/", import.meta.url)
  },
  {
    name: "desktop",
    command: "pnpm",
    args: ["--filter", "@aurabot/desktop", "dev"],
    cwd: new URL("../", import.meta.url),
    env: {
      ELECTRON_RENDERER_URL: "http://127.0.0.1:6400"
    }
  }
];

const children = processes.map((entry) => {
  const child = spawn(entry.command, entry.args, {
    cwd: entry.cwd,
    env: {
      ...process.env,
      ...entry.env
    },
    stdio: "inherit"
  });

  child.on("exit", (code, signal) => {
    if (signal) {
      console.log(`${entry.name} exited from ${signal}`);
    } else if (code !== 0) {
      console.log(`${entry.name} exited with code ${code}`);
    }
    shutdown();
  });

  return child;
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => shutdown(signal));
}

function shutdown(signal) {
  for (const child of children) {
    if (!child.killed) {
      child.kill(signal ?? "SIGTERM");
    }
  }
}
