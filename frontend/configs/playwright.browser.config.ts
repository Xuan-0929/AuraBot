/** Runs layout/scroll regressions against the compiled Renderer in headless Chromium.
 * A single static preview server serves all tests; typed IPC fixtures replace desktop processes.
 * Separate output preserves desktop failure artifacts when the full suite runs sequentially. */
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

const port = 6402;
const baseURL = `http://127.0.0.1:${port}`;
export default defineConfig({
  testDir: '../tests/browser',
  outputDir: '../test-results/browser',
  timeout: 30_000,
  workers: 1,
  reporter: [['list']],
  use: { baseURL, headless: true, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: {
    command: `npx vite preview --outDir out/renderer --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: fileURLToPath(new URL('..', import.meta.url)),
    // TCP readiness avoids sending loopback health checks through inherited HTTP proxies.
    port,
    reuseExistingServer: false
  }
});
