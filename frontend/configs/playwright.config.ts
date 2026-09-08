/** Serializes Electron E2E processes because the application owns one fixed Backend port.
 * Tests use isolated data roots but cannot run concurrent Backend listeners on port 6401. */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../tests/e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    trace: "retain-on-failure"
  }
});
