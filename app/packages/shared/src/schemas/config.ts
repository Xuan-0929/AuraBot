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

export const taskCliRunnerIdSchema = z.enum(["codex", "claude", "hermes"]);
export const taskRunnerSandboxSchema = z.enum(["read-only", "workspace-write", "danger-full-access"]);
export const taskRunnerApprovalPolicySchema = z.enum(["untrusted", "on-request", "never", "on-failure"]);

export const taskCliRunnerConfigSchema = z.object({
  id: taskCliRunnerIdSchema,
  enabled: z.boolean().default(false),
  priority: z.number().int().positive().default(100),
  command: z.string().min(1).optional(),
  workingDirectory: z.string().min(1).optional(),
  model: z.string().min(1).optional(),
  sandbox: taskRunnerSandboxSchema.default("workspace-write"),
  approvalPolicy: taskRunnerApprovalPolicySchema.default("never"),
  timeoutMs: z.number().int().positive().default(300000),
  extraArgs: z.array(z.string()).default([])
});

export const taskRunnerConfigSchema = z.object({
  provider: z.enum(["deterministic", "codex", "cli"]).default("deterministic"),
  runners: z.array(taskCliRunnerConfigSchema).default([]),
  codexCommand: z.string().min(1).optional(),
  workingDirectory: z.string().min(1).optional(),
  model: z.string().min(1).optional(),
  sandbox: taskRunnerSandboxSchema.default("workspace-write"),
  approvalPolicy: taskRunnerApprovalPolicySchema.default("never"),
  timeoutMs: z.number().int().positive().default(300000)
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
  taskRunner: taskRunnerConfigSchema,
  proactive: proactiveConfigSchema
});

export type ModelRoleConfig = z.infer<typeof modelRoleConfigSchema>;
export type TaskCliRunnerId = z.infer<typeof taskCliRunnerIdSchema>;
export type TaskCliRunnerConfig = z.infer<typeof taskCliRunnerConfigSchema>;
export type TaskRunnerConfig = z.infer<typeof taskRunnerConfigSchema>;
export type ProactiveConfig = z.infer<typeof proactiveConfigSchema>;
export type AppConfig = z.infer<typeof appConfigSchema>;
