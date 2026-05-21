import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { notificationSettingsSchema } from "../../../packages/shared/src/schemas/notificationSettings";
import type { NotificationSettings } from "../../../packages/shared/src/contracts/notificationSettings";

const SETTINGS_FILE_NAME = "notification-settings.json";

export class NotificationSettingsStore {
  private settings: Omit<NotificationSettings, "permissionState">;
  private readonly filePath: string;

  constructor(private readonly userDataPath: string) {
    this.filePath = join(userDataPath, SETTINGS_FILE_NAME);
    this.settings = this.load();
  }

  get(permissionState: NotificationSettings["permissionState"] = "system_managed"): NotificationSettings {
    return {
      ...this.settings,
      permissionState
    };
  }

  update(
    patch: Partial<Omit<NotificationSettings, "permissionState">>,
    permissionState: NotificationSettings["permissionState"] = "system_managed"
  ): NotificationSettings {
    this.settings = notificationSettingsSchema.parse({
      ...this.settings,
      ...patch
    });
    this.persist();

    return this.get(permissionState);
  }

  private load(): Omit<NotificationSettings, "permissionState"> {
    if (!existsSync(this.filePath)) {
      return notificationSettingsSchema.parse({});
    }

    try {
      return notificationSettingsSchema.parse(JSON.parse(readFileSync(this.filePath, "utf8")));
    } catch {
      return notificationSettingsSchema.parse({});
    }
  }

  private persist(): void {
    mkdirSync(this.userDataPath, { recursive: true });
    writeFileSync(this.filePath, `${JSON.stringify(this.settings, null, 2)}\n`, "utf8");
  }
}
