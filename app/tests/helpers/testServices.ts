import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ChatOrchestrator } from "../../packages/core-legacy/src/orchestration/chatOrchestrator";
import { createServices, type AppServices } from "../../packages/core-legacy/src/bootstrap/createServices";

interface TestServiceHandle {
  dir: string;
  services: AppServices;
}

const serviceHandles: TestServiceHandle[] = [];

export function createTestServices(prefix: string): AppServices {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  const previousDeterministicModelFallback = process.env.AURABOT_DETERMINISTIC_MODEL_FALLBACK;

  process.env.AURABOT_DETERMINISTIC_MODEL_FALLBACK = "1";

  let services: AppServices;

  try {
    services = createServices(dir);
  } finally {
    if (previousDeterministicModelFallback === undefined) {
      delete process.env.AURABOT_DETERMINISTIC_MODEL_FALLBACK;
    } else {
      process.env.AURABOT_DETERMINISTIC_MODEL_FALLBACK = previousDeterministicModelFallback;
    }
  }

  serviceHandles.push({ dir, services });

  return services;
}

export function createTestOrchestrator(prefix: string): {
  services: AppServices;
  orchestrator: ChatOrchestrator;
} {
  const services = createTestServices(prefix);

  return {
    services,
    orchestrator: new ChatOrchestrator(services)
  };
}

export function cleanupTestServices(): void {
  for (const handle of serviceHandles.splice(0)) {
    handle.services.database.close();
    rmSync(handle.dir, { force: true, recursive: true });
  }
}
