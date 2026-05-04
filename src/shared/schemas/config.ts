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

export const appConfigSchema = z.object({
  storagePath: z.string().min(1),
  debugLogging: z.boolean().default(true),
  gateModel: modelRoleConfigSchema,
  chatModel: modelRoleConfigSchema
});

export type ModelRoleConfig = z.infer<typeof modelRoleConfigSchema>;
export type AppConfig = z.infer<typeof appConfigSchema>;
