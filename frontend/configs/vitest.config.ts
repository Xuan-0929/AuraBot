/** Runs Node/Vue unit regressions without Electron; script and browser suites have separate entries.
 * Shared aliases match production imports, while forks isolate globals and native SQLite handles. */
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  test: {
    globals: true,
    environment: "node",
    include: ["tests/unit/**/*.spec.ts"],
    pool: "forks"
  },
  resolve: {
    alias: {
      "@core": new URL("../src/core", import.meta.url).pathname,
      "@shared": new URL("../src/shared", import.meta.url).pathname,
      "@renderer": new URL("../src", import.meta.url).pathname
    }
  }
});
