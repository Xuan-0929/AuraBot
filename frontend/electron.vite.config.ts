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
        input: resolve(__dirname, "index.html"),
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
