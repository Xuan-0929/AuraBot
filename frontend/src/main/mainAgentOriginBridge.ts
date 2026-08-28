/**
 * Grants the packaged Pet renderer a narrowly scoped trusted Origin for the local Main Agent gateway.
 * Only the registered Pet webContents and exact http/ws 127.0.0.1:6401 targets are rewritten to the
 * backend-allowed development origin; Console, Settings, external pages, null/file origins, and other
 * loopback hosts or ports remain untouched. Electron's request hook is the sole caller in production.
 */

import type { BrowserWindow } from "electron";

const TRUSTED_RENDERER_ORIGIN = "http://127.0.0.1:6400";
const GATEWAY_HOST = "127.0.0.1:6401";

export interface MainAgentRequestDetails {
  url: string;
  webContentsId: number | undefined;
  requestHeaders: Record<string, string | string[]>;
}

export function normalizedMainAgentHeaders(
  details: MainAgentRequestDetails,
  petWebContentsId: number,
): Record<string, string | string[]> | null {
  if (details.webContentsId !== petWebContentsId) return null;
  let target: URL;
  try {
    target = new URL(details.url);
  } catch {
    return null;
  }
  if (
    (target.protocol !== "http:" && target.protocol !== "ws:")
    || target.host !== GATEWAY_HOST
  ) {
    return null;
  }
  return { ...details.requestHeaders, Origin: TRUSTED_RENDERER_ORIGIN };
}

export function installMainAgentPetOriginBridge(window: BrowserWindow): void {
  const petWebContentsId = window.webContents.id;
  window.webContents.session.webRequest.onBeforeSendHeaders(
    {
      urls: [
        "http://127.0.0.1:6401/*",
        "ws://127.0.0.1:6401/*",
      ],
    },
    (details, callback) => {
      const requestHeaders = normalizedMainAgentHeaders(
        {
          url: details.url,
          webContentsId: details.webContentsId,
          requestHeaders: details.requestHeaders,
        },
        petWebContentsId,
      );
      callback(requestHeaders === null ? {} : { requestHeaders });
    },
  );
}
