/**
 * 通用聊天消息 schema：校验主会话角色、正文与历史附件名称投影。
 * 新消息不再接受附件输入；SQLite 中的旧 metadata 只读解析安全名称字段，不向
 * Renderer 暴露绝对路径、Task 标识或文件操作协议。
 */
import { z } from "zod";
import { isoDateStringSchema } from "./memory";

export const messageRoleSchema = z.enum(["user", "assistant", "system", "tool"]);

export const messageAttachmentSchema = z.object({
  id: z.string().min(1),
  kind: z.enum(["file", "image"]),
  purpose: z.enum(["input", "output"]).optional(),
  name: z.string().min(1)
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
