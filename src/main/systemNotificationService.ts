import type { NotificationSettings } from "../shared/contracts/notificationSettings";
import type { PetActivityKind } from "../shared/contracts/pet";
import type { PetSettings } from "../shared/schemas/petSettings";

export type SystemNotificationKind = Extract<PetActivityKind, "memory_follow_up" | "task_reminder" | "proactive_idle">;

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
    if (!this.dependencies.isSupported()) {
      return false;
    }

    if (!input.settings.systemNotificationsEnabled) {
      return false;
    }

    if (input.kind === "memory_follow_up" && !input.settings.followUpNotificationsEnabled) {
      return false;
    }

    if (input.kind === "task_reminder" && !input.settings.timeReminderNotificationsEnabled) {
      return false;
    }

    if (input.kind === "proactive_idle" && !input.settings.idleNudgeNotificationsEnabled) {
      return false;
    }

    const notification = this.dependencies.createNotification(
      buildSystemNotificationPayload(input.kind, input.message, this.dependencies.getDisplayLanguage())
    );

    notification.on("click", () => {
      this.dependencies.openConsoleWindow();
    });
    notification.show();

    return true;
  }
}

function buildSystemNotificationPayload(
  kind: SystemNotificationKind,
  message: string,
  displayLanguage: PetSettings["displayLanguage"]
): SystemNotificationPayload {
  if (displayLanguage === "zh") {
    switch (kind) {
      case "memory_follow_up":
        return {
          title: "Aura 想提醒你一下",
          body: message
        };
      case "task_reminder":
        return {
          title: "Aura 到点来叫你了",
          body: message
        };
      case "proactive_idle":
        return {
          title: "Aura 轻轻冒个泡",
          body: message
        };
    }
  }

  switch (kind) {
    case "memory_follow_up":
      return {
        title: "Aura has a little reminder",
        body: message
      };
    case "task_reminder":
      return {
        title: "Aura is nudging you on time",
        body: message
      };
    case "proactive_idle":
      return {
        title: "Aura is lightly checking in",
        body: message
      };
  }
}
