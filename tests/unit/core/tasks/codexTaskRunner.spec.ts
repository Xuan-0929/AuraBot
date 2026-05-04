import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CliCodexAgentClient,
  CodexTaskRunner,
  type CodexAgentClient
} from "../../../../src/core/tasks/codexTaskRunner";
import { TaskRepository } from "../../../../src/core/storage/repositories/taskRepository";
import { cleanupTestServices, createTestServices } from "../../../helpers/testServices";

afterEach(cleanupTestServices);

describe("CodexTaskRunner", () => {
  it("runs a child Codex agent and stores the final result on the task", async () => {
    const services = createTestServices("aurabot-codex-runner-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我做一份 GitHub 周报。"
    });
    const taskRepository = new TaskRepository(services.database);
    const client: CodexAgentClient = {
      run: vi.fn(async (input) => {
        input.onProgress?.({
          summary: "Codex 子任务会话已建立：thread-1",
          payload: {
            type: "thread.started",
            threadId: "thread-1"
          }
        });

        return {
          finalMessage: "GitHub 周报：本周完成了 Live2D 桌宠和任务主线程控制。",
          stdout: "stdout",
          stderr: ""
        };
      })
    };
    const runner = new CodexTaskRunner(taskRepository, services.eventLogService, client, {
      command: "codex",
      workingDirectory: "/Users/xuan/project",
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 300000,
      outputDirectory: "/tmp/aurabot-test"
    });

    const completed = await runner.run(task);
    const events = taskRepository.listEvents(task.id);
    const eventLogs = services.eventLogService.list(20);

    expect(client.run).toHaveBeenCalledWith(
      expect.objectContaining({
        taskId: task.id,
        cwd: "/Users/xuan/project",
        prompt: expect.stringContaining("帮我做一份 GitHub 周报。")
      })
    );
    expect(completed?.status).toBe("completed");
    expect(completed?.latestProgress).toContain("GitHub 周报");
    expect(events.map((event) => event.eventType)).toEqual([
      "task.running",
      "task.agent.started",
      "task.agent.progress",
      "task.completed"
    ]);
    expect(events[2]?.summary).toContain("thread-1");
    expect(eventLogs.some((log) => log.eventType === "task.agent.completed")).toBe(true);
  });

  it("marks the task failed when the child Codex agent fails", async () => {
    const services = createTestServices("aurabot-codex-runner-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我做一份 GitHub 周报。"
    });
    const taskRepository = new TaskRepository(services.database);
    const client: CodexAgentClient = {
      run: vi.fn(async () => {
        throw new Error("codex exited 1");
      })
    };
    const runner = new CodexTaskRunner(taskRepository, services.eventLogService, client, {
      command: "codex",
      workingDirectory: "/Users/xuan/project",
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 300000,
      outputDirectory: "/tmp/aurabot-test"
    });

    const failed = await runner.run(task);
    const events = taskRepository.listEvents(task.id);
    const eventLogs = services.eventLogService.list(20);

    expect(failed?.status).toBe("failed");
    expect(failed?.latestProgress).toContain("codex exited 1");
    expect(events.map((event) => event.eventType)).toEqual([
      "task.running",
      "task.agent.started",
      "task.failed"
    ]);
    expect(eventLogs.some((log) => log.eventType === "task.agent.failed")).toBe(true);
  });

  it("keeps an interrupted task interrupted when the child run is cancelled", async () => {
    const services = createTestServices("aurabot-codex-runner-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我做一份 GitHub 周报。"
    });
    const taskRepository = new TaskRepository(services.database);
    const client: CodexAgentClient = {
      run: vi.fn(
        (input) =>
          new Promise<Awaited<ReturnType<CodexAgentClient["run"]>>>((_resolve, reject) => {
            input.signal?.addEventListener("abort", () => {
              reject(new Error("codex task cancelled"));
            });
          })
      )
    };
    const runner = new CodexTaskRunner(taskRepository, services.eventLogService, client, {
      command: "codex",
      workingDirectory: "/Users/xuan/project",
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 300000,
      outputDirectory: "/tmp/aurabot-test"
    });

    const runPromise = runner.run(task);
    await new Promise((resolve) => setTimeout(resolve, 0));
    taskRepository.updateStatus({
      taskId: task.id,
      status: "interrupted",
      latestProgress: "用户在主聊天线程要求停掉"
    });
    expect(runner.cancel(task.id)).toBe(true);
    const interrupted = await runPromise;
    const events = taskRepository.listEvents(task.id);

    expect(interrupted?.status).toBe("interrupted");
    expect(events.map((event) => event.eventType)).toContain("task.agent.cancelled");
    expect(events.map((event) => event.eventType)).not.toContain("task.failed");
  });
});

describe("CliCodexAgentClient", () => {
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

      const result = await new CliCodexAgentClient().run({
        taskId: "task-1",
        prompt: "请做 GitHub 周报",
        cwd: dir,
        outputFile,
        command: fakeCodexPath,
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 3000
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
process.stdout.write(JSON.stringify({ type: "thread.started", thread_id: "thread-1" }) + "\\n");
process.stdout.write(JSON.stringify({ type: "turn.started" }) + "\\n");
process.stdout.write(JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: "OK" } }) + "\\n");
process.stdout.write(JSON.stringify({ type: "item.completed", item: { type: "command_execution", command: "/bin/zsh -lc pwd", exit_code: 0, aggregated_output: "/tmp/project\\n" } }) + "\\n");
process.stdout.write(JSON.stringify({ type: "item.completed", item: { type: "file_change", changes: [{ path: "/tmp/project/report.md", kind: "add" }] } }) + "\\n");
fs.writeFileSync(outputFile, "OK", "utf8");
`,
        "utf8"
      );
      chmodSync(fakeCodexPath, 0o755);
      const progress: string[] = [];

      const result = await new CliCodexAgentClient().run({
        taskId: "task-1",
        prompt: "请做 GitHub 周报",
        cwd: dir,
        outputFile,
        command: fakeCodexPath,
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 1000,
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

  it("kills the Codex child process when the run signal is aborted", async () => {
    const dir = mkdtempSync(join(tmpdir(), "aurabot-fake-codex-cancel-"));

    try {
      const fakeCodexPath = join(dir, "fake-codex-cancel.cjs");
      const outputFile = join(dir, "last-message.md");
      writeFileSync(
        fakeCodexPath,
        `#!/usr/bin/env node
process.on("SIGTERM", () => {
  process.stderr.write("received sigterm");
  process.exit(143);
});
setInterval(() => {}, 1000);
`,
        "utf8"
      );
      chmodSync(fakeCodexPath, 0o755);
      const abortController = new AbortController();
      const runPromise = new CliCodexAgentClient().run({
        taskId: "task-1",
        prompt: "请做 GitHub 周报",
        cwd: dir,
        outputFile,
        command: fakeCodexPath,
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 5000,
        signal: abortController.signal
      });

      abortController.abort();

      await expect(runPromise).rejects.toThrow(/cancelled|exited/);
    } finally {
      rmSync(dir, { force: true, recursive: true });
    }
  });
});
