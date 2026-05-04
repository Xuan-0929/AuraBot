export class TaskInterruptionController {
  private readonly interruptedTaskIds = new Set<string>();

  interrupt(taskId: string): void {
    this.interruptedTaskIds.add(taskId);
  }

  isInterrupted(taskId: string): boolean {
    return this.interruptedTaskIds.has(taskId);
  }
}
