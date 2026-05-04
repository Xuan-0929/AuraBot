export const MEMORY_NODE_TYPES = ["trait", "preference", "event", "follow_up"] as const;

export const MEMORY_STATES = ["active", "stale", "completed", "archived"] as const;

export const MEMORY_EDGE_TYPES = [
  "relates_to",
  "caused_by",
  "scheduled_for",
  "preference_for",
  "opposes"
] as const;

export const DEFAULT_MEMORY_TRAVERSAL_DEPTH = 2;
export const DEFAULT_MEMORY_TRAVERSAL_LIMIT = 8;
