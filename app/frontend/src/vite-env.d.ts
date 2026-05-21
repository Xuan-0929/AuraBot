/// <reference types="vite/client" />

import type { AuraBotApi } from "@aurabot/shared/contracts/ipc";

declare global {
  interface Window {
    aurabot: AuraBotApi;
  }
}
