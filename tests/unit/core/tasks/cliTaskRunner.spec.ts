import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CliAgentClientImpl,
  MultiCliTaskRunner,
  buildCliAgentArgs,
  type CliAgentClient,
  type CliAgentRunInput
} from "../../../../src/core/tasks/cliTaskRunner";
import { TaskRepository } from "../../../../src/core/storage/repositories/taskRepository";
import { cleanupTestServices, createTestServices } from "../../../helpers/testServices";

afterEach(cleanupTestServices);

describe("MultiCliTaskRunner", () => {
  it("runs the enabled task CLI with the highest priority", async () => {
    const services = createTestServices("aurabot-cli-runner-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "写一份实践论读书笔记"
    });
    const taskRepository = new TaskRepository(services.database);
    const client: CliAgentClient = {
      run: vi.fn(async (input: CliAgentRunInput) => ({
        finalMessage: `${input.runnerLabel} 已经写好了。`,
        stdout: "stdout",
        stderr: ""
      }))
    };
    const runner = new MultiCliTaskRunner(taskRepository, services.eventLogService, client, {
      outputDirectory: "/tmp/aurabot-cli-test",
      runners: [
        {
          id: "codex",
          enabled: true,
          priority: 2,
          command: "codex",
          workingDirectory: "/Users/xuan/project",
          sandbox: "workspace-write",
          approvalPolicy: "never",
          timeoutMs: 300000,
          extraArgs: []
        },
        {
          id: "claude",
          enabled: true,
          priority: 1,
          command: "claude",
          workingDirectory: "/Users/xuan/project",
          sandbox: "workspace-write",
          approvalPolicy: "never",
          timeoutMs: 300000,
          extraArgs: []
        }
      ],
      isCommandAvailable: () => true
    });

    const completed = await runner.run(task);

    expect(client.run).toHaveBeenCalledWith(
      expect.objectContaining({
        runnerId: "claude",
        runnerLabel: "Claude Code",
        command: "claude"
      })
    );
    expect(completed?.status).toBe("completed");
    expect(completed?.latestProgress).toContain("Claude Code 已经写好了。");
  });

  it("skips unavailable higher-priority CLIs and uses the next enabled runner", async () => {
    const services = createTestServices("aurabot-cli-runner-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "写一份 GitHub 周报"
    });
    const taskRepository = new TaskRepository(services.database);
    const client: CliAgentClient = {
      run: vi.fn(async (input: CliAgentRunInput) => ({
        finalMessage: `${input.runnerLabel} 完成了任务。`,
        stdout: "stdout",
        stderr: ""
      }))
    };
    const runner = new MultiCliTaskRunner(taskRepository, services.eventLogService, client, {
      outputDirectory: "/tmp/aurabot-cli-test",
      runners: [
        {
          id: "hermes",
          enabled: true,
          priority: 1,
          command: "missing-hermes",
          workingDirectory: "/Users/xuan/project",
          sandbox: "workspace-write",
          approvalPolicy: "never",
          timeoutMs: 300000,
          extraArgs: []
        },
        {
          id: "codex",
          enabled: true,
          priority: 2,
          command: "codex",
          workingDirectory: "/Users/xuan/project",
          sandbox: "workspace-write",
          approvalPolicy: "never",
          timeoutMs: 300000,
          extraArgs: []
        }
      ],
      isCommandAvailable: (command) => command !== "missing-hermes"
    });

    const completed = await runner.run(task);
    const events = taskRepository.listEvents(task.id);

    expect(client.run).toHaveBeenCalledWith(expect.objectContaining({ runnerId: "codex" }));
    expect(completed?.status).toBe("completed");
    expect(events.map((event) => event.eventType)).toContain("task.agent.skipped");
  });
});

describe("CliAgentClientImpl", () => {
  it("runs a codex-compatible command with the prompt on stdin and reads output-last-message", async () => {
    const dir = mkdtempSync(join(tmpdir(), "aurabot-fake-codex-"));

    try {
      const fakeCodexPath = join(dir, "fake-codex.cjs");
      const outputFile = join(dir, "last-message.md");
      writeFileSync(
        fakeCodexPath,
        `#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
const outputIndex = args.indexOf("--output-last-message");
const outputFile = args[outputIndex + 1];
let stdin = "";
process.stdin.on("data", (chunk) => {
  stdin += chunk.toString("utf8");
});
process.stdin.on("end", () => {
  fs.writeFileSync(outputFile, "子任务最终结果：" + stdin.includes("GitHub 周报"), "utf8");
  process.stdout.write(args.join("\\n"));
});
`,
        "utf8"
      );
      chmodSync(fakeCodexPath, 0o755);

      const result = await new CliAgentClientImpl().run({
        taskId: "task-1",
        runnerId: "codex",
        runnerLabel: "Codex",
        prompt: "请做 GitHub 周报",
        cwd: dir,
        outputFile,
        command: fakeCodexPath,
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 3000,
        extraArgs: []
      });
      const args = result.stdout.split("\n");

      expect(result.finalMessage).toBe("子任务最终结果：true");
      expect(args.slice(0, 3)).toEqual(["--ask-for-approval", "never", "exec"]);
      expect(result.stdout).toContain("--json");
      expect(result.stdout).toContain("--sandbox");
      expect(result.stdout).toContain("workspace-write");
    } finally {
      rmSync(dir, { force: true, recursive: true });
    }
  });

  it("maps Codex JSONL events to progress callbacks", async () => {
    const dir = mkdtempSync(join(tmpdir(), "aurabot-fake-codex-json-"));

    try {
      const fakeCodexPath = join(dir, "fake-codex-json.cjs");
      const outputFile = join(dir, "last-message.md");
      writeFileSync(
        fakeCodexPath,
        `#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
const outputIndex = args.indexOf("--output-last-message");
const outputFile = args[outputIndex + 1];
process.stdin.resume();
process.stdin.on("end", () => {
  process.stdout.write(JSON.stringify({ type: "thread.started", thread_id: "thread-1" }) + "\\n");
  process.stdout.write(JSON.stringify({ type: "turn.started" }) + "\\n");
  process.stdout.write(JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: "OK" } }) + "\\n");
  process.stdout.write(JSON.stringify({ type: "item.completed", item: { type: "command_execution", command: "/bin/zsh -lc pwd", exit_code: 0, aggregated_output: "/tmp/project\\n" } }) + "\\n");
  process.stdout.write(JSON.stringify({ type: "item.completed", item: { type: "file_change", changes: [{ path: "/tmp/project/report.md", kind: "add" }] } }) + "\\n");
  fs.writeFileSync(outputFile, "OK", "utf8");
});
`,
        "utf8"
      );
      chmodSync(fakeCodexPath, 0o755);
      const progress: string[] = [];

      const result = await new CliAgentClientImpl().run({
        taskId: "task-1",
        runnerId: "codex",
        runnerLabel: "Codex",
        prompt: "请做 GitHub 周报",
        cwd: dir,
        outputFile,
        command: fakeCodexPath,
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 3000,
        extraArgs: [],
        onProgress: (entry) => progress.push(entry.summary)
      });

      expect(result.finalMessage).toBe("OK");
      expect(progress).toEqual([
        "Codex 子任务会话已建立：thread-1",
        "Codex 子任务开始处理任务。",
        "Codex 子任务生成回复内容。",
        "Codex 子任务完成命令：/bin/zsh -lc pwd",
        "Codex 子任务新增文件：/tmp/project/report.md"
      ]);
    } finally {
      rmSync(dir, { force: true, recursive: true });
    }
  });

  it("kills the child process when the run signal is aborted", async () => {
    const dir = mkdtempSync(join(tmpdir(), "aurabot-fake-cli-cancel-"));

    try {
      const fakeCliPath = join(dir, "fake-cli-cancel.cjs");
      const outputFile = join(dir, "last-message.md");
      writeFileSync(
        fakeCliPath,
        `#!/usr/bin/env node
process.on("SIGTERM", () => {
  process.stderr.write("received sigterm");
  process.exit(143);
});
setInterval(() => {}, 1000);
`,
        "utf8"
      );
      chmodSync(fakeCliPath, 0o755);
      const abortController = new AbortController();
      const runPromise = new CliAgentClientImpl().run({
        taskId: "task-1",
        runnerId: "claude",
        runnerLabel: "Claude Code",
        prompt: "请做 GitHub 周报",
        cwd: dir,
        outputFile,
        command: fakeCliPath,
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 5000,
        extraArgs: [],
        signal: abortController.signal
      });

      abortController.abort();

      await expect(runPromise).rejects.toThrow(/cancelled|exited/);
    } finally {
      rmSync(dir, { force: true, recursive: true });
    }
  });
});

describe("buildCliAgentArgs", () => {
  it("builds Claude Code and Hermes command arguments", () => {
    expect(
      buildCliAgentArgs({
        taskId: "task-1",
        runnerId: "claude",
        runnerLabel: "Claude Code",
        prompt: "任务",
        cwd: "/tmp/project",
        outputFile: "/tmp/output.md",
        command: "claude",
        model: "sonnet",
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 1000,
        extraArgs: ["--no-session-persistence"]
      })
    ).toEqual([
      "--print",
      "--output-format",
      "text",
      "--permission-mode",
      "bypassPermissions",
      "--model",
      "sonnet",
      "--no-session-persistence"
    ]);

    expect(
      buildCliAgentArgs({
        taskId: "task-1",
        runnerId: "hermes",
        runnerLabel: "Hermes",
        prompt: "任务",
        cwd: "/tmp/project",
        outputFile: "/tmp/output.md",
        command: "hermes",
        model: "fast",
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 1000,
        extraArgs: ["run"]
      })
    ).toEqual(["run", "--model", "fast"]);
  });
});
