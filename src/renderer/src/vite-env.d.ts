/// <reference types="vite/client" />

import type { AuraBotApi } from "../../shared/contracts/ipc";

declare global {
  interface Window {
    aurabot: AuraBotApi;
  }
}
