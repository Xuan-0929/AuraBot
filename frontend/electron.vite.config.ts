/**
 * Configures Electron main/preload builds plus both the production Renderer and standalone realtime
 * protocol lab HTML entry. Shared aliases keep application code stable while the diagnostic entry remains
 * dependency-free and is emitted beside the normal index page.
 */

import { resolve } from "node:path";
import vue from "@vitejs/plugin-vue";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, "src/main/index.ts"),
      },
    },
    resolve: {
      alias: {
        "@core": resolve(__dirname, "src/core"),
        "@shared": resolve(__dirname, "src/shared"),
      },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, "src/preload/index.ts"),
        output: {
          format: "cjs",
          entryFileNames: "index.js",
        },
      },
    },
    resolve: {
      alias: {
        "@shared": resolve(__dirname, "src/shared"),
      },
    },
  },
  renderer: {
    root: resolve(__dirname, "."),
    plugins: [vue()],
    server: {
      host: "127.0.0.1",
      port: 6400,
      strictPort: true,
    },
    build: {
      rollupOptions: {
        input: {
          app: resolve(__dirname, "index.html"),
          realtimeTestClient: resolve(__dirname, "realtime-test-client.html"),
        },
      },
    },
    resolve: {
      alias: {
        "@renderer": resolve(__dirname, "src"),
        "@shared": resolve(__dirname, "src/shared"),
      },
    },
  },
});
