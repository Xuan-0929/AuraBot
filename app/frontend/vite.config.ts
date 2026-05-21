import { resolve } from "node:path";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 6400,
    strictPort: true
  },
  resolve: {
    alias: {
      "@frontend": resolve(__dirname, "src"),
      "@aurabot/shared": resolve(__dirname, "../packages/shared/src")
    }
  }
});
