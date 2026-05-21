import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, test } from "vitest";
import { NotificationSettingsStore } from "../../../desktop/src/main/notificationSettingsStore";

describe("NotificationSettingsStore", () => {
  test("loads conservative defaults when no settings file exists", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-notification-settings-"));

    try {
      const store = new NotificationSettingsStore(userDataPath);

      expect(store.get()).toMatchObject({
        systemNotificationsEnabled: true,
        followUpNotificationsEnabled: true,
        timeReminderNotificationsEnabled: true,
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
