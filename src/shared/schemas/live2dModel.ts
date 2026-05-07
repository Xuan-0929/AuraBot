import { z } from "zod";

export const live2dModelDescriptorSchema = z.object({
  id: z.string().min(1),
  displayName: z.string().min(1),
  folderName: z.string().min(1),
  manifestFileName: z.string().min(1),
  manifestPath: z.string().min(1)
});

export const live2dModelStateSchema = z.object({
  currentModelId: z.string().min(1),
  currentModelPath: z.string().min(1),
  models: z.array(live2dModelDescriptorSchema).default([])
});

export const live2dModelSettingsSchema = z.object({
  currentModelId: z.string().min(1).default("hiyori")
});

export type Live2DModelSettings = z.infer<typeof live2dModelSettingsSchema>;
