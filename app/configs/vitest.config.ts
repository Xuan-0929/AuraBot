import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  test: {
    globals: true,
    environment: "node",
    include: ["../tests/unit/**/*.spec.ts", "../tests/integration/**/*.spec.ts"],
    pool: "forks"
  },
  resolve: {
    alias: {
      "@core": new URL("../packages/core-legacy/src", import.meta.url).pathname,
      "@shared": new URL("../packages/shared/src", import.meta.url).pathname,
      "@aurabot/core-legacy": new URL("../packages/core-legacy/src", import.meta.url).pathname,
      "@aurabot/shared": new URL("../packages/shared/src", import.meta.url).pathname,
      "@frontend": new URL("../frontend/src", import.meta.url).pathname
    }
  }
});
