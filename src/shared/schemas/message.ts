import { z } from "zod";
import { isoDateStringSchema } from "./memory";

export const messageRoleSchema = z.enum(["user", "assistant", "system", "tool"]);

export const messageAttachmentSchema = z.object({
  id: z.string().min(1),
  kind: z.enum(["file", "image"]),
  purpose: z.enum(["input", "output"]).optional(),
  taskId: z.string().min(1).optional(),
  name: z.string().min(1),
  path: z.string().min(1).optional()
});

export const messageSchema = z.object({
  id: z.string().min(1),
  sessionId: z.string().min(1),
  profileId: z.string().min(1),
  role: messageRoleSchema,
  content: z.string(),
  attachments: z.array(messageAttachmentSchema).optional().default([]),
  createdAt: isoDateStringSchema
});

export type MessageAttachment = z.infer<typeof messageAttachmentSchema>;
export type Message = Omit<z.infer<typeof messageSchema>, "attachments"> & {
  attachments?: MessageAttachment[];
};
export type MessageRole = z.infer<typeof messageRoleSchema>;
