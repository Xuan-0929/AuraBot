export type NotificationPermissionState = "system_managed" | "unsupported";

export interface NotificationSettings {
  systemNotificationsEnabled: boolean;
  permissionState: NotificationPermissionState;
  followUpNotificationsEnabled: boolean;
  timeReminderNotificationsEnabled: boolean;
  idleNudgeNotificationsEnabled: boolean;
}
