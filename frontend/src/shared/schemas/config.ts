/**
 * 定义 Electron 当前实际读取的应用配置 schema。
 *
 * schema 只包含 Chat/Gate/Memory 模型、SQLite 路径、日志与非 Task 主动能力；旧
 * taskRunner 字段由 Zod 作为未知输入忽略，不再解析或映射。
 */

import { z } from "zod";

export const modelRoleConfigSchema = z.object({
  provider: z.string().min(1).default("openai-compatible"),
  model: z.string().min(1).default("model"),
  enabled: z.boolean().default(false),
  baseUrl: z.string().url().optional(),
  apiKey: z.string().min(1).optional(),
  temperature: z.number().min(0).max(2).optional(),
  timeoutMs: z.number().int().positive().optional()
});

export const proactiveConfigSchema = z.object({
  pollIntervalMs: z.number().int().positive().default(10000),
  followUpLookaheadMs: z.number().int().nonnegative().default(0),
  idleThresholdMs: z.number().int().positive().default(600000),
  idleCooldownMs: z.number().int().positive().default(1800000)
});

export const appConfigSchema = z.object({
  storagePath: z.string().min(1),
  debugLogging: z.boolean().default(true),
  gateModel: modelRoleConfigSchema,
  chatModel: modelRoleConfigSchema,
  memoryModel: modelRoleConfigSchema,
  proactive: proactiveConfigSchema
});

export type ModelRoleConfig = z.infer<typeof modelRoleConfigSchema>;
export type ProactiveConfig = z.infer<typeof proactiveConfigSchema>;
export type AppConfig = z.infer<typeof appConfigSchema>;
