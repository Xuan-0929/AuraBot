import { z } from "zod";

export const modelRoleConfigSchema = z.object({
  provider: z.string().min(1).default("local-stub"),
  model: z.string().min(1).default("stub"),
  enabled: z.boolean().default(false)
});

export const appConfigSchema = z.object({
  storagePath: z.string().min(1),
  debugLogging: z.boolean().default(true),
  gateModel: modelRoleConfigSchema,
  chatModel: modelRoleConfigSchema
});

export type AppConfig = z.infer<typeof appConfigSchema>;
