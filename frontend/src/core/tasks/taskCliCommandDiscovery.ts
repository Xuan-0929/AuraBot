import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
import type { TaskCliRunnerId } from "@shared/schemas/config";

export function detectTaskCliCommand(
  id: TaskCliRunnerId,
  options: {
    platform?: NodeJS.Platform;
    pathValue?: string;
  } = {}
): string | undefined {
  for (const candidate of getTaskCliCommandCandidates(id, options.platform ?? process.platform)) {
    const resolved = resolveCommandCandidate(candidate, {
      platform: options.platform,
      pathValue: options.pathValue
    });

    if (resolved) {
      return resolved;
    }
  }

  return undefined;
}

export function getTaskCliCommandCandidates(id: TaskCliRunnerId, platform: NodeJS.Platform = process.platform): string[] {
  if (platform === "darwin") {
    if (id === "codex") {
      return ["/Applications/Codex.app/Contents/Resources/codex", "codex"];
    }

    if (id === "claude") {
      return ["/opt/homebrew/bin/claude", "claude"];
    }

    return ["/opt/homebrew/bin/hermes", "hermes"];
  }

  if (platform === "win32") {
    return [`${id}.cmd`, `${id}.exe`, id];
  }

  return [id];
}

export function resolveCommandCandidate(
  candidate: string,
  options: {
    platform?: NodeJS.Platform;
    pathValue?: string;
  } = {}
): string | undefined {
  if (isPathLikeCommand(candidate)) {
    return existsSync(candidate) ? candidate : undefined;
  }

  const pathEntries = (options.pathValue ?? process.env.PATH ?? "")
    .split(getPathDelimiter(options.platform ?? process.platform))
    .map((entry) => entry.trim())
    .filter(Boolean);

  for (const entry of pathEntries) {
    const resolved = join(entry, candidate);

    if (existsSync(resolved)) {
      return resolved;
    }
  }

  return undefined;
}

function getPathDelimiter(platform: NodeJS.Platform): string {
  return platform === "win32" ? ";" : delimiter;
}

function isPathLikeCommand(candidate: string): boolean {
  return candidate.includes("/") || candidate.includes("\\");
}
