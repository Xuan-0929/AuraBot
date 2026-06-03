import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  detectTaskCliCommand,
  getTaskCliCommandCandidates,
  resolveCommandCandidate
} from "@core/tasks/taskCliCommandDiscovery";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { force: true, recursive: true });
  }
});

function createTempBin(): string {
  const dir = mkdtempSync(join(tmpdir(), "aurabot-cli-discovery-"));
  tempDirs.push(dir);

  return dir;
}

describe("task CLI command discovery", () => {
  it("keeps macOS Codex and Homebrew CLI candidates first", () => {
    expect(getTaskCliCommandCandidates("codex", "darwin")).toEqual([
      "/Applications/Codex.app/Contents/Resources/codex",
      "codex"
    ]);
    expect(getTaskCliCommandCandidates("claude", "darwin")).toEqual(["/opt/homebrew/bin/claude", "claude"]);
    expect(getTaskCliCommandCandidates("hermes", "darwin")).toEqual(["/opt/homebrew/bin/hermes", "hermes"]);
  });

  it("uses Windows command extensions and semicolon PATH entries", () => {
    const binDir = createTempBin();
    const commandPath = join(binDir, "codex.cmd");
    writeFileSync(commandPath, "", "utf8");

    expect(getTaskCliCommandCandidates("codex", "win32")).toEqual(["codex.cmd", "codex.exe", "codex"]);
    expect(detectTaskCliCommand("codex", { platform: "win32", pathValue: `${binDir};C:\\OtherBin` })).toBe(commandPath);
  });

  it("resolves explicit path-like commands without PATH lookup", () => {
    const binDir = createTempBin();
    const commandPath = join(binDir, "hermes.exe");
    writeFileSync(commandPath, "", "utf8");

    expect(resolveCommandCandidate(commandPath, { platform: "win32", pathValue: "" })).toBe(commandPath);
  });
});
