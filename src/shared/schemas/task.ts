import { z } from "zod";
import { isoDateStringSchema } from "./memory";

export const taskStatusSchema = z.enum([
  "queued",
  "running",
  "waiting",
  "completed",
  "failed",
  "interrupted"
]);

export const taskSchema = z.object({
  id: z.string().min(1),
  profileId: z.string().min(1),
  parentSessionId: z.string().min(1),
  sessionId: z.string().min(1),
  title: z.string().min(1),
  status: taskStatusSchema,
  latestProgress: z.string().default(""),
  createdAt: isoDateStringSchema,
  updatedAt: isoDateStringSchema
});

export const taskEventSchema = z.object({
  id: z.string().min(1),
  taskId: z.string().min(1),
  eventType: z.string().min(1),
  summary: z.string().min(1),
  payload: z.record(z.string(), z.unknown()).default({}),
  createdAt: isoDateStringSchema
});

export type Task = z.infer<typeof taskSchema>;
export type TaskStatus = z.infer<typeof taskStatusSchema>;
export type TaskEvent = z.infer<typeof taskEventSchema>;
