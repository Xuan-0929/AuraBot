/**
 * 通知设置存储测试：覆盖保留字段的默认值、持久化和重新加载。
 * 期望中不包含历史定时 Task reminder 设置。
 */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, test } from "vitest";
import { NotificationSettingsStore } from "../../../src/main/notificationSettingsStore";

describe("NotificationSettingsStore", () => {
  test("loads conservative defaults when no settings file exists", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-notification-settings-"));

    try {
      const store = new NotificationSettingsStore(userDataPath);

      expect(store.get()).toMatchObject({
        systemNotificationsEnabled: true,
        followUpNotificationsEnabled: true,
        idleNudgeNotificationsEnabled: false
      });
    } finally {
      rmSync(userDataPath, { force: true, recursive: true });
    }
  });

  test("persists notification settings updates", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-notification-settings-"));

    try {
      const store = new NotificationSettingsStore(userDataPath);
      const updated = store.update({
        idleNudgeNotificationsEnabled: true,
        followUpNotificationsEnabled: false
      });

      expect(updated).toMatchObject({
        idleNudgeNotificationsEnabled: true,
        followUpNotificationsEnabled: false
      });

      const reloaded = new NotificationSettingsStore(userDataPath);
      expect(reloaded.get()).toMatchObject({
        idleNudgeNotificationsEnabled: true,
        followUpNotificationsEnabled: false
      });
    } finally {
      rmSync(userDataPath, { force: true, recursive: true });
    }
  });
});
