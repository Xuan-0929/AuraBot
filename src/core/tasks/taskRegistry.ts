export interface TaskRegistryEntry {
  id: string;
  label: string;
}

export class TaskRegistry {
  private readonly entries = new Map<string, TaskRegistryEntry>();

  register(entry: TaskRegistryEntry): void {
    this.entries.set(entry.id, entry);
  }

  list(): TaskRegistryEntry[] {
    return [...this.entries.values()];
  }
}
