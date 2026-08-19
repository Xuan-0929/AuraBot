/**
 * 通用聊天消息 schema：校验主会话中的角色、正文与用户附件元数据。
 * 消息由 renderer、IPC 与 SQLite repository 共享；附件不携带 Task 标识或执行产物协议。
 */
import { z } from "zod";
import { isoDateStringSchema } from "./memory";

export const messageRoleSchema = z.enum(["user", "assistant", "system", "tool"]);

export const messageAttachmentSchema = z.object({
  id: z.string().min(1),
  kind: z.enum(["file", "image"]),
  purpose: z.enum(["input", "output"]).optional(),
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
