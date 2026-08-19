/**
 * 系统通知设置契约：控制全局通知、Memory 跟进和空闲主动消息。
 * 设置由主进程持久化并投影到 renderer；定时 Task 提醒不属于保留能力。
 */
export type NotificationPermissionState = "system_managed" | "unsupported";

export interface NotificationSettings {
  systemNotificationsEnabled: boolean;
  permissionState: NotificationPermissionState;
  followUpNotificationsEnabled: boolean;
  idleNudgeNotificationsEnabled: boolean;
}
