import { describe, expect, it, vi } from "vitest";
import type { NotificationSettings } from "@shared/contracts/notificationSettings";
import { SystemNotificationService } from "../../../src/main/systemNotificationService";

const baseSettings: NotificationSettings = {
  systemNotificationsEnabled: true,
  permissionState: "system_managed",
  followUpNotificationsEnabled: true,
  timeReminderNotificationsEnabled: true,
  idleNudgeNotificationsEnabled: false
};

describe("SystemNotificationService", () => {
  it("delivers a follow-up notification when support and switches are enabled", () => {
    const show = vi.fn();
    const on = vi.fn();
    const openConsoleWindow = vi.fn();
    const createNotification = vi.fn(() => ({ show, on }));
    const service = new SystemNotificationService({
      isSupported: () => true,
      createNotification,
      openConsoleWindow,
      getDisplayLanguage: () => "zh"
    });

    const delivered = service.deliver({
      kind: "memory_follow_up",
      message: "提醒你一下，今晚的算法面试别忘了。",
      settings: baseSettings
    });

    expect(delivered).toBe(true);
    expect(createNotification).toHaveBeenCalledWith({
      title: "Aura 想提醒你一下",
      body: "提醒你一下，今晚的算法面试别忘了。"
    });
    expect(on).toHaveBeenCalledWith("click", expect.any(Function));
    expect(show).toHaveBeenCalledTimes(1);
    const clickHandler = on.mock.calls[0]?.[1] as (() => void) | undefined;
    clickHandler?.();
    expect(openConsoleWindow).toHaveBeenCalledTimes(1);
  });

  it("suppresses unsupported environments and disabled switches", () => {
    const createNotification = vi.fn();
    const service = new SystemNotificationService({
      isSupported: () => false,
      createNotification,
      openConsoleWindow: vi.fn(),
      getDisplayLanguage: () => "en"
    });

    expect(
      service.deliver({
        kind: "task_reminder",
        message: "Time to check the report.",
        settings: baseSettings
      })
    ).toBe(false);
    expect(createNotification).not.toHaveBeenCalled();

    const supportedService = new SystemNotificationService({
      isSupported: () => true,
      createNotification,
      openConsoleWindow: vi.fn(),
      getDisplayLanguage: () => "en"
    });

    expect(
      supportedService.deliver({
        kind: "proactive_idle",
        message: "I'm still here if you want to keep going.",
        settings: {
          ...baseSettings,
          idleNudgeNotificationsEnabled: false
        }
      })
    ).toBe(false);
    expect(createNotification).not.toHaveBeenCalled();
  });
});
