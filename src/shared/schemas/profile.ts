import { z } from "zod";
import { isoDateStringSchema } from "./memory";

export const soulSchema = z.object({
  id: z.string().min(1),
  profileId: z.string().min(1),
  name: z.string().min(1),
  persona: z.string().min(1),
  tone: z.string().min(1),
  principles: z.array(z.string().min(1)).default([]),
  taboos: z.array(z.string().min(1)).default([]),
  speakingStyle: z.string().min(1),
  relationship: z.string().min(1),
  boundaries: z.array(z.string().min(1)).default([]),
  topicPreferences: z.array(z.string().min(1)).default([]),
  createdAt: isoDateStringSchema,
  updatedAt: isoDateStringSchema
});

export const profileSchema = z.object({
  id: z.string().min(1),
  displayName: z.string().min(1),
  active: z.boolean(),
  createdAt: isoDateStringSchema,
  updatedAt: isoDateStringSchema,
  soul: soulSchema.optional()
});

export type Soul = z.infer<typeof soulSchema>;
export type Profile = z.infer<typeof profileSchema>;
