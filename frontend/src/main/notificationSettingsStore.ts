/**
 * 通知设置存储：在 userData 下读写保留的桌面通知偏好。
 * 读取时通过共享 schema 丢弃历史未知字段，更新后向 IPC 返回包含平台权限状态的投影。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { notificationSettingsSchema } from "@shared/schemas/notificationSettings";
import type { NotificationSettings } from "@shared/contracts/notificationSettings";

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
