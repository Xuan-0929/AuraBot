/**
 * 通知设置校验：为保留的系统通知、Memory 跟进与 idle nudge 提供默认值。
 * 主进程设置存储使用该 schema；历史 JSON 中的 Task reminder 字段会作为未知字段被忽略。
 */
import { z } from "zod";

export const notificationSettingsSchema = z.object({
  systemNotificationsEnabled: z.boolean().default(true),
  followUpNotificationsEnabled: z.boolean().default(true),
  idleNudgeNotificationsEnabled: z.boolean().default(false)
});

export type NotificationSettingsInput = z.input<typeof notificationSettingsSchema>;
