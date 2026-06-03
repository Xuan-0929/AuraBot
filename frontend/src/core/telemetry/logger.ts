export function logInfo(message: string, context: Record<string, unknown> = {}): void {
  console.info(`[AuraBot] ${message}`, context);
}
