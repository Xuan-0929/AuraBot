import { z } from "zod";
import { MEMORY_EDGE_TYPES, MEMORY_NODE_TYPES, MEMORY_STATES } from "../constants/memory";

export const memoryNodeTypeSchema = z.enum(MEMORY_NODE_TYPES);
export const memoryStateSchema = z.enum(MEMORY_STATES);
export const memoryEdgeTypeSchema = z.enum(MEMORY_EDGE_TYPES);

export const isoDateStringSchema = z.string().datetime({ offset: true });

export const memoryNodeSchema = z.object({
  id: z.string().min(1),
  profileId: z.string().min(1),
  type: memoryNodeTypeSchema,
  state: memoryStateSchema,
  content: z.string().min(1),
  importance: z.number().min(0).max(1),
  startsAt: isoDateStringSchema.optional(),
  endsAt: isoDateStringSchema.optional(),
  createdAt: isoDateStringSchema,
  updatedAt: isoDateStringSchema
});

export const memoryEdgeSchema = z.object({
  id: z.string().min(1),
  profileId: z.string().min(1),
  sourceNodeId: z.string().min(1),
  targetNodeId: z.string().min(1),
  relationType: memoryEdgeTypeSchema,
  weight: z.number().min(0).max(1),
  createdAt: isoDateStringSchema
});

export const memorySummarySchema = z.object({
  id: z.string().min(1),
  profileId: z.string().min(1),
  topic: z.string().min(1),
  content: z.string().min(1),
  updatedAt: isoDateStringSchema
});

export const memoryWriteRequestSchema = z.object({
  profileId: z.string().min(1),
  sourceSessionId: z.string().min(1),
  node: z.object({
    type: memoryNodeTypeSchema,
    content: z.string().min(1),
    importance: z.number().min(0).max(1),
    startsAt: isoDateStringSchema.optional(),
    endsAt: isoDateStringSchema.optional()
  }),
  edges: z
    .array(
      z.object({
        targetNodeId: z.string().min(1),
        relationType: memoryEdgeTypeSchema,
        weight: z.number().min(0).max(1)
      })
    )
    .default([])
});

export type MemoryNode = z.infer<typeof memoryNodeSchema>;
export type MemoryEdge = z.infer<typeof memoryEdgeSchema>;
export type MemorySummary = z.infer<typeof memorySummarySchema>;
export type MemoryWriteRequest = z.infer<typeof memoryWriteRequestSchema>;
