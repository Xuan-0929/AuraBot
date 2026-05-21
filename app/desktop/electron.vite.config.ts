import { resolve } from "node:path";
import vue from "@vitejs/plugin-vue";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, "src/main/index.ts")
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, "src/preload/index.ts"),
        output: {
          format: "cjs",
          entryFileNames: "index.js"
        }
      }
    }
  },
  renderer: {
    root: resolve(__dirname, "../frontend"),
    plugins: [vue()],
    server: {
      host: "127.0.0.1",
      port: 6400,
      strictPort: true
    },
    build: {
      rollupOptions: {
        input: resolve(__dirname, "../frontend/index.html")
      }
    },
    resolve: {
      alias: {
        "@frontend": resolve(__dirname, "../frontend/src"),
        "@aurabot/shared": resolve(__dirname, "../packages/shared/src")
      }
    }
  }
});
