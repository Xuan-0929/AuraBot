import { z } from "zod";

export const notificationSettingsSchema = z.object({
  systemNotificationsEnabled: z.boolean().default(true),
  followUpNotificationsEnabled: z.boolean().default(true),
  timeReminderNotificationsEnabled: z.boolean().default(true),
  idleNudgeNotificationsEnabled: z.boolean().default(false)
});

export type NotificationSettingsInput = z.input<typeof notificationSettingsSchema>;
