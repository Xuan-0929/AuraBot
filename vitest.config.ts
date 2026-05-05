import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    include: ["tests/unit/**/*.spec.ts", "tests/unit/**/*.spec.tsx", "tests/integration/**/*.spec.ts"],
    pool: "forks"
  },
  resolve: {
    alias: {
      "@core": new URL("./src/core", import.meta.url).pathname,
      "@shared": new URL("./src/shared", import.meta.url).pathname
    }
  }
});
