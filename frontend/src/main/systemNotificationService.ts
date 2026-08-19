/**
 * 系统通知服务：把 Memory 跟进与 idle nudge 活动投递为桌面通知。
 * 主进程注入平台通知工厂和 Console 打开动作；服务按用户设置过滤并生成本地化标题，
 * 不接收或翻译 Task 生命周期与定时提醒事件。
 */
import type { NotificationSettings } from "@shared/contracts/notificationSettings";
import type { PetActivityKind } from "@shared/contracts/pet";
import type { PetSettings } from "@shared/schemas/petSettings";

export type SystemNotificationKind = Extract<PetActivityKind, "memory_follow_up" | "proactive_idle">;

export interface SystemNotificationHandle {
  show(): void;
  on(event: "click", listener: () => void): void;
}

export interface SystemNotificationPayload {
  title: string;
  body: string;
}

export interface SystemNotificationServiceDependencies {
  isSupported(): boolean;
  createNotification(payload: SystemNotificationPayload): SystemNotificationHandle;
  openConsoleWindow(): void;
  getDisplayLanguage(): PetSettings["displayLanguage"];
}

export class SystemNotificationService {
  constructor(private readonly dependencies: SystemNotificationServiceDependencies) {}

  deliver(input: {
    kind: SystemNotificationKind;
    message: string;
    settings: NotificationSettings;
  }): boolean {
    if (!this.dependencies.isSupported() || !input.settings.systemNotificationsEnabled) {
      return false;
    }

    if (input.kind === "memory_follow_up" && !input.settings.followUpNotificationsEnabled) {
      return false;
    }

    if (input.kind === "proactive_idle" && !input.settings.idleNudgeNotificationsEnabled) {
      return false;
    }

    const notification = this.dependencies.createNotification(
      buildSystemNotificationPayload(input.kind, input.message, this.dependencies.getDisplayLanguage())
    );
    notification.on("click", () => this.dependencies.openConsoleWindow());
    notification.show();
    return true;
  }
}

function buildSystemNotificationPayload(
  kind: SystemNotificationKind,
  message: string,
  displayLanguage: PetSettings["displayLanguage"]
): SystemNotificationPayload {
  const titles = {
    zh: {
      memory_follow_up: "Aura 想提醒你一下",
      proactive_idle: "Aura 轻轻冒个泡"
    },
    ja: {
      memory_follow_up: "Aura から小さなリマインダー",
      proactive_idle: "Aura がそっと声をかけています"
    },
    en: {
      memory_follow_up: "Aura has a little reminder",
      proactive_idle: "Aura is lightly checking in"
    }
  } as const;

  return {
    title: titles[displayLanguage][kind],
    body: message
  };
}
