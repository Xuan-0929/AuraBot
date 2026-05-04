import { z } from "zod";
import { isoDateStringSchema } from "./memory";

export const messageRoleSchema = z.enum(["user", "assistant", "system", "tool"]);

export const messageSchema = z.object({
  id: z.string().min(1),
  sessionId: z.string().min(1),
  profileId: z.string().min(1),
  role: messageRoleSchema,
  content: z.string(),
  createdAt: isoDateStringSchema
});

export type Message = z.infer<typeof messageSchema>;
export type MessageRole = z.infer<typeof messageRoleSchema>;
