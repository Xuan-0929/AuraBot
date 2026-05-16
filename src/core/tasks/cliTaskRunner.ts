import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { delimiter, dirname, join, resolve } from "node:path";
import type { TaskCliRunnerConfig, TaskCliRunnerId } from "../../shared/schemas/config";
import type { Task } from "../../shared/schemas/task";
import { getTaskCliRunnerLabel } from "../../shared/contracts/settings";
import type { TaskRepository } from "../storage/repositories/taskRepository";
import type { EventLogService } from "../telemetry/eventLogService";
import type { TaskRunner, TaskRunnerObserver } from "./taskRunner";

export interface CliAgentRunInput {
  taskId: string;
  runnerId: TaskCliRunnerId;
  runnerLabel: string;
  prompt: string;
  cwd: string;
  outputFile: string;
  command: string;
  model?: string;
  sandbox: "read-only" | "workspace-write" | "danger-full-access";
  approvalPolicy: "untrusted" | "on-request" | "never" | "on-failure";
  timeoutMs: number;
  extraArgs: string[];
  onProgress?: (progress: CliAgentProgress) => void;
  signal?: AbortSignal;
}

export interface CliAgentRunResult {
  finalMessage: string;
  stdout: string;
  stderr: string;
}

export interface CliAgentProgress {
  summary: string;
  payload?: Record<string, unknown>;
}

export interface CliAgentClient {
  run(input: CliAgentRunInput): Promise<CliAgentRunResult>;
}

export interface MultiCliTaskRunnerOptions {
  runners: TaskCliRunnerConfig[];
  outputDirectory: string;
  isCommandAvailable?: (command: string) => boolean;
  isTrustedWorkingDirectory?: (runner: TaskCliRunnerConfig, cwd: string) => boolean;
}

export class MultiCliTaskRunner implements TaskRunner {
  private readonly activeRuns = new Map<string, AbortController>();

  constructor(
    private readonly taskRepository: TaskRepository,
    private readonly eventLogService: EventLogService,
    private readonly cliAgentClient: CliAgentClient,
    private readonly options: MultiCliTaskRunnerOptions
  ) {}

  async run(task: Task, observer?: TaskRunnerObserver): Promise<Task | undefined> {
    const selection = this.selectRunner(task);
    const runner = selection.runner;

    if (!runner) {
      return this.failTask(
        task,
        selection.failureSummary ??
          "没有可用的任务 CLI。请在设置里启用 Codex、Claude Code 或 Hermes，并确认命令路径可用。"
      );
    }

    const runnerLabel = getTaskCliRunnerLabel(runner.id);
    const abortController = new AbortController();
    this.activeRuns.set(task.id, abortController);
    const startedAt = new Date();
    const runningSummary = `开始调用 ${runnerLabel} 子任务：${task.title}`;
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.running",
      summary: runningSummary,
      payload: {
        runnerId: runner.id,
        startedAt: startedAt.toISOString()
      }
    });
    const running = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "running",
      latestProgress: runningSummary
    });

    if (running) {
      observer?.onProgress?.(running);
    }

    mkdirSync(this.options.outputDirectory, { recursive: true });
    const outputFile = join(this.options.outputDirectory, `${task.id}-${runner.id}-last-message.md`);
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.agent.started",
      summary: `${runnerLabel} 子任务已启动：${task.title}`,
      payload: {
        runnerId: runner.id,
        cwd: runner.workingDirectory,
        outputFile,
        sandbox: runner.sandbox,
        command: runner.command,
        model: runner.model,
        approvalPolicy: runner.approvalPolicy,
        startedAt: startedAt.toISOString()
      }
    });

    try {
      const result = await this.cliAgentClient.run({
        taskId: task.id,
        runnerId: runner.id,
        runnerLabel,
        prompt: buildCliTaskPrompt(task, runnerLabel),
        cwd: runner.workingDirectory ?? process.cwd(),
        outputFile,
        command: runner.command ?? runner.id,
        model: runner.model,
        sandbox: runner.sandbox,
        approvalPolicy: runner.approvalPolicy,
        timeoutMs: runner.timeoutMs,
        extraArgs: runner.extraArgs,
        signal: abortController.signal,
        onProgress: (progress) => {
          this.taskRepository.appendEvent({
            taskId: task.id,
            eventType: "task.agent.progress",
            summary: progress.summary,
            payload: progress.payload
          });
          const progressed = this.taskRepository.updateStatus({
            taskId: task.id,
            status: "running",
            latestProgress: progress.summary
          });

          if (progressed) {
            observer?.onProgress?.(progressed);
          }
        }
      });
      const finalMessage = normalizeAgentFinalMessage(result.finalMessage, runnerLabel);
      const completedSummary = `${runnerLabel} 子任务完成：\n${finalMessage}`;
      const completedAt = new Date();
      this.taskRepository.appendEvent({
        taskId: task.id,
        eventType: "task.completed",
        summary: completedSummary,
        payload: {
          runnerId: runner.id,
          outputFile,
          startedAt: startedAt.toISOString(),
          completedAt: completedAt.toISOString(),
          durationMs: completedAt.getTime() - startedAt.getTime(),
          stdout: result.stdout,
          stderr: result.stderr
        }
      });
      const completed = this.taskRepository.updateStatus({
        taskId: task.id,
        status: "completed",
        latestProgress: completedSummary
      });

      if (completed) {
        this.eventLogService.record("task.agent.completed", {
          profileId: completed.profileId,
          taskId: completed.id,
          title: completed.title,
          outputFile,
          runnerId: runner.id,
          status: completed.status
        });
      }

      return completed;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const currentTask = this.taskRepository.getById(task.id);

      if (currentTask?.status === "interrupted") {
        const completedAt = new Date();
        this.taskRepository.appendEvent({
          taskId: task.id,
          eventType: "task.agent.cancelled",
          summary: `${runnerLabel} 子任务已停止：${message}`,
          payload: {
            runnerId: runner.id,
            startedAt: startedAt.toISOString(),
            completedAt: completedAt.toISOString(),
            durationMs: completedAt.getTime() - startedAt.getTime()
          }
        });
        return currentTask;
      }

      return this.failTask(task, `${runnerLabel} 子任务失败：${message}`, runner.id, startedAt);
    } finally {
      this.activeRuns.delete(task.id);
    }
  }

  cancel(taskId: string): boolean {
    const abortController = this.activeRuns.get(taskId);

    if (!abortController) {
      return false;
    }

    abortController.abort();
    return true;
  }

  private selectRunner(task: Task): {
    runner?: TaskCliRunnerConfig;
    failureSummary?: string;
  } {
    const sortedRunners = this.options.runners
      .filter((runner) => runner.enabled)
      .slice()
      .sort((left, right) => left.priority - right.priority);
    let lastFailureSummary: string | undefined;

    for (const runner of sortedRunners) {
      const command = runner.command ?? runner.id;
      const cwd = runner.workingDirectory ?? process.cwd();

      if (this.isCommandAvailable(command)) {
        if (!this.isTrustedWorkingDirectory(runner, cwd)) {
          const reason = buildWorkingDirectoryValidationMessage(runner.id, cwd);
          lastFailureSummary = reason;
          this.taskRepository.appendEvent({
            taskId: task.id,
            eventType: "task.agent.skipped",
            summary: reason,
            payload: {
              runnerId: runner.id,
              command,
              cwd
            }
          });
          continue;
        }

        return {
          runner: {
            ...runner,
            command,
            workingDirectory: cwd
          }
        };
      }

      lastFailureSummary = `${getTaskCliRunnerLabel(runner.id)} 命令不可用。请确认命令路径可用，或切到下一个任务 CLI。`;
      this.taskRepository.appendEvent({
        taskId: task.id,
        eventType: "task.agent.skipped",
        summary: `${getTaskCliRunnerLabel(runner.id)} 命令不可用，切到下一个任务 CLI。`,
        payload: {
          runnerId: runner.id,
          command
        }
      });
    }

    return {
      failureSummary: lastFailureSummary
    };
  }

  private isCommandAvailable(command: string): boolean {
    return this.options.isCommandAvailable?.(command) ?? isCommandAvailable(command);
  }

  private isTrustedWorkingDirectory(runner: TaskCliRunnerConfig, cwd: string): boolean {
    return this.options.isTrustedWorkingDirectory?.(runner, cwd) ?? isTrustedWorkingDirectory(runner, cwd);
  }

  private failTask(task: Task, summary: string, runnerId?: TaskCliRunnerId, startedAt?: Date): Task | undefined {
    const completedAt = new Date();
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.failed",
      summary,
      payload: {
        ...(runnerId ? { runnerId } : {}),
        ...(startedAt
          ? {
              startedAt: startedAt.toISOString(),
              completedAt: completedAt.toISOString(),
              durationMs: completedAt.getTime() - startedAt.getTime()
            }
          : {})
      }
    });
    const failed = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "failed",
      latestProgress: summary
    });

    if (failed) {
      this.eventLogService.record("task.agent.failed", {
        profileId: failed.profileId,
        taskId: failed.id,
        title: failed.title,
        status: failed.status,
        reason: summary,
        runnerId
      });
    }

    return failed;
  }
}

export class CliAgentClientImpl implements CliAgentClient {
  async run(input: CliAgentRunInput): Promise<CliAgentRunResult> {
    return new Promise((resolve, reject) => {
      const args = buildCliAgentArgs(input);
      const child = spawn(input.command, args, {
        cwd: input.cwd,
        env: {
          ...process.env,
          AURABOT_CHILD_TASK_ID: input.taskId,
          AURABOT_CHILD_TASK_RUNNER: input.runnerId
        },
        stdio: ["pipe", "pipe", "pipe"]
      });
      let stdout = "";
      let stderr = "";
      let stdoutLineBuffer = "";
      let timedOut = false;
      let cancelled = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        child.kill("SIGTERM");
      }, input.timeoutMs);
      const abortRun = () => {
        cancelled = true;
        child.kill("SIGTERM");
      };

      if (input.signal?.aborted) {
        abortRun();
      } else {
        input.signal?.addEventListener("abort", abortRun, { once: true });
      }

      child.stdout.on("data", (chunk: Buffer) => {
        const text = chunk.toString("utf8");
        stdout += text;

        if (input.runnerId === "codex") {
          stdoutLineBuffer = consumeCodexJsonlProgress(stdoutLineBuffer + text, input.onProgress);
        }
      });
      child.stderr.on("data", (chunk: Buffer) => {
        stderr += chunk.toString("utf8");
      });
      child.on("error", (error) => {
        clearTimeout(timeout);
        input.signal?.removeEventListener("abort", abortRun);
        reject(error);
      });
      child.on("close", (code, signal) => {
        clearTimeout(timeout);
        input.signal?.removeEventListener("abort", abortRun);

        if (timedOut) {
          reject(new Error(`${input.runnerLabel} task timed out after ${input.timeoutMs}ms`));
          return;
        }

        if (cancelled) {
          reject(new Error(`${input.runnerLabel} task cancelled`));
          return;
        }

        if (code !== 0) {
          reject(new Error(`${input.runnerLabel} exited ${code ?? `with signal ${signal ?? "unknown"}`}: ${stderr.trim()}`.trim()));
          return;
        }

        resolve({
          finalMessage: readFinalMessage(input, stdout),
          stdout,
          stderr
        });
      });
      child.stdin.end(input.prompt);
    });
  }
}

export function buildCliAgentArgs(input: CliAgentRunInput): string[] {
  if (input.runnerId === "codex") {
    const args = [
      "--ask-for-approval",
      input.approvalPolicy,
      "exec",
      "--cd",
      input.cwd,
      "--sandbox",
      input.sandbox,
      "--json",
      "--output-last-message",
      input.outputFile,
      "--color",
      "never"
    ];

    if (input.model) {
      args.push("--model", input.model);
    }

    args.push(...input.extraArgs, "-");
    return args;
  }

  if (input.runnerId === "claude") {
    const args = ["--print", "--output-format", "text", "--permission-mode", mapClaudePermissionMode(input.approvalPolicy)];

    if (input.model) {
      args.push("--model", input.model);
    }

    args.push(...input.extraArgs);
    return args;
  }

  const args = [...input.extraArgs];

  if (input.model) {
    args.push("--model", input.model);
  }

  return args;
}

function buildCliTaskPrompt(task: Task, runnerLabel: string): string {
  return [
    `你是 AuraBot 派出的子任务 ${runnerLabel} agent。`,
    "你运行在独立任务线程中，主聊天线程会等待你的最终结果并回流给用户。",
    "请只处理下面这个任务，不要修改与任务无关的文件。",
    "如果需要读取仓库、调用 git/gh、生成报告或写入 artifact，可以在当前工作目录内完成。",
    "最终回复请使用中文，给出用户可以直接阅读的结果；如果创建或修改了文件，请列出路径。",
    "",
    `任务标题：${task.title}`,
    `任务 ID：${task.id}`
  ].join("\n");
}

function readFinalMessage(input: CliAgentRunInput, stdout: string): string {
  if (existsSync(input.outputFile)) {
    return readFileSync(input.outputFile, "utf8").trim();
  }

  return stdout.trim();
}

function mapClaudePermissionMode(
  approvalPolicy: "untrusted" | "on-request" | "never" | "on-failure"
): "default" | "dontAsk" | "bypassPermissions" {
  if (approvalPolicy === "never") {
    return "bypassPermissions";
  }

  if (approvalPolicy === "on-request") {
    return "dontAsk";
  }

  return "default";
}

function consumeCodexJsonlProgress(
  value: string,
  onProgress: ((progress: CliAgentProgress) => void) | undefined
): string {
  const lines = value.split(/\r?\n/);
  const remaining = lines.pop() ?? "";

  for (const line of lines) {
    const progress = mapCodexJsonlEventToProgress(line);

    if (progress) {
      onProgress?.(progress);
    }
  }

  return remaining;
}

function mapCodexJsonlEventToProgress(line: string): CliAgentProgress | undefined {
  const trimmed = line.trim();

  if (!trimmed.startsWith("{")) {
    return undefined;
  }

  try {
    const event = JSON.parse(trimmed) as Record<string, unknown>;
    const type = typeof event.type === "string" ? event.type : undefined;

    if (type === "thread.started") {
      const threadId = typeof event.thread_id === "string" ? event.thread_id : undefined;

      return {
        summary: threadId ? `Codex 子任务会话已建立：${threadId}` : "Codex 子任务会话已建立。",
        payload: {
          type,
          threadId
        }
      };
    }

    if (type === "turn.started") {
      return {
        summary: "Codex 子任务开始处理任务。",
        payload: { type }
      };
    }

    if (type === "turn.completed") {
      return {
        summary: "Codex 子任务本轮处理完成，正在收尾。",
        payload: { type }
      };
    }

    if (type === "item.completed") {
      const item = isRecord(event.item) ? event.item : {};

      return {
        summary: summarizeCompletedCodexItem(item),
        payload: buildCompletedItemPayload(type, item)
      };
    }
  } catch {
    return undefined;
  }

  return undefined;
}

function summarizeCompletedCodexItem(item: Record<string, unknown>): string {
  const itemType = typeof item.type === "string" ? item.type : undefined;

  if (itemType === "agent_message") {
    return "Codex 子任务生成回复内容。";
  }

  if (itemType === "tool_call") {
    return "Codex 子任务完成一次工具调用。";
  }

  if (itemType === "command_execution") {
    const command = typeof item.command === "string" ? item.command : undefined;

    return command ? `Codex 子任务完成命令：${command}` : "Codex 子任务完成一次命令执行。";
  }

  if (itemType === "file_change") {
    const firstChange = getFirstFileChange(item);

    if (firstChange?.path) {
      const action = firstChange.kind === "add" ? "新增" : firstChange.kind === "delete" ? "删除" : "修改";

      return `Codex 子任务${action}文件：${firstChange.path}`;
    }

    return "Codex 子任务产生文件改动。";
  }

  return itemType ? `Codex 子任务完成事件：${itemType}` : "Codex 子任务有新的执行进展。";
}

function buildCompletedItemPayload(type: string, item: Record<string, unknown>): Record<string, unknown> {
  const itemType = typeof item.type === "string" ? item.type : undefined;
  const payload: Record<string, unknown> = {
    type,
    itemType
  };

  if (typeof item.command === "string") {
    payload.command = item.command;
  }

  if (typeof item.exit_code === "number") {
    payload.exitCode = item.exit_code;
  }

  const output = typeof item.aggregated_output === "string" ? item.aggregated_output.trim() : undefined;
  if (output) {
    payload.outputPreview = output.slice(0, 500);
  }

  const firstChange = getFirstFileChange(item);
  if (firstChange) {
    payload.fileChange = firstChange;
  }

  return payload;
}

function getFirstFileChange(item: Record<string, unknown>): { path?: string; kind?: string } | undefined {
  const changes = Array.isArray(item.changes) ? item.changes : [];
  const firstChange = changes.find(isRecord);

  if (!firstChange) {
    return undefined;
  }

  return {
    path: typeof firstChange.path === "string" ? firstChange.path : undefined,
    kind: typeof firstChange.kind === "string" ? firstChange.kind : undefined
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeAgentFinalMessage(value: string, runnerLabel: string): string {
  const trimmed = value.trim();

  return trimmed || `${runnerLabel} 子任务完成，但没有返回最终文本。`;
}

function isCommandAvailable(command: string): boolean {
  if (command.includes("/")) {
    return existsSync(command);
  }

  return (process.env.PATH ?? "")
    .split(delimiter)
    .filter(Boolean)
    .some((entry) => existsSync(join(entry, command)));
}

function isTrustedWorkingDirectory(runner: TaskCliRunnerConfig, cwd: string): boolean {
  if (runner.id !== "codex") {
    return true;
  }

  return findGitRepositoryRoot(cwd) !== undefined;
}

function findGitRepositoryRoot(value: string): string | undefined {
  let current = resolve(value);

  while (true) {
    if (existsSync(join(current, ".git"))) {
      return current;
    }

    const parent = dirname(current);

    if (parent === current) {
      return undefined;
    }

    current = parent;
  }
}

function buildWorkingDirectoryValidationMessage(runnerId: TaskCliRunnerId, cwd: string): string {
  if (runnerId === "codex") {
    return `Codex 当前工作目录不可用：${cwd} 不是受信任的 Git 仓库目录。请把任务工作目录改到实际项目仓库后再试。`;
  }

  return `${getTaskCliRunnerLabel(runnerId)} 当前工作目录不可用：${cwd}。`;
}
