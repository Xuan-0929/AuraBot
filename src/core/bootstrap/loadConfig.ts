import { join } from "node:path";
import type { AppConfig } from "../../shared/schemas/config";

export function loadConfig(userDataPath: string): AppConfig {
  return {
    storagePath: join(userDataPath, "aurabot.sqlite"),
    debugLogging: true,
    gateModel: {
      provider: "local-stub",
      model: "gate-stub",
      enabled: false
    },
    chatModel: {
      provider: "local-stub",
      model: "chat-stub",
      enabled: false
    }
  };
}
