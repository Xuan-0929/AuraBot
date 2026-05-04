import { z } from "zod";

export const modelRoleConfigSchema = z.object({
  provider: z.string().min(1).default("local-stub"),
  model: z.string().min(1).default("stub"),
  enabled: z.boolean().default(false),
  baseUrl: z.string().url().optional(),
  apiKey: z.string().min(1).optional(),
  temperature: z.number().min(0).max(2).optional(),
  timeoutMs: z.number().int().positive().optional()
});

export const taskRunnerConfigSchema = z.object({
  provider: z.enum(["deterministic", "codex"]).default("deterministic"),
  codexCommand: z.string().min(1).optional(),
  workingDirectory: z.string().min(1).optional(),
  model: z.string().min(1).optional(),
  sandbox: z.enum(["read-only", "workspace-write", "danger-full-access"]).default("workspace-write"),
  approvalPolicy: z.enum(["untrusted", "on-request", "never", "on-failure"]).default("never"),
  timeoutMs: z.number().int().positive().default(300000)
});

export const appConfigSchema = z.object({
  storagePath: z.string().min(1),
  debugLogging: z.boolean().default(true),
  gateModel: modelRoleConfigSchema,
  chatModel: modelRoleConfigSchema,
  memoryModel: modelRoleConfigSchema,
  taskRunner: taskRunnerConfigSchema
});

export type ModelRoleConfig = z.infer<typeof modelRoleConfigSchema>;
export type TaskRunnerConfig = z.infer<typeof taskRunnerConfigSchema>;
export type AppConfig = z.infer<typeof appConfigSchema>;
