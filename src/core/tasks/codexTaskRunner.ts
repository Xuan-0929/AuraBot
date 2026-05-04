import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Task } from "../../shared/schemas/task";
import type { EventLogService } from "../telemetry/eventLogService";
import type { TaskRepository } from "../storage/repositories/taskRepository";
import type { TaskRunner, TaskRunnerObserver } from "./taskRunner";

export interface CodexAgentRunInput {
  taskId: string;
  prompt: string;
  cwd: string;
  outputFile: string;
  command: string;
  model?: string;
  sandbox: "read-only" | "workspace-write" | "danger-full-access";
  approvalPolicy: "untrusted" | "on-request" | "never" | "on-failure";
  timeoutMs: number;
  onProgress?: (progress: CodexAgentProgress) => void;
  signal?: AbortSignal;
}

export interface CodexAgentRunResult {
  finalMessage: string;
  stdout: string;
  stderr: string;
}

export interface CodexAgentProgress {
  summary: string;
  payload?: Record<string, unknown>;
}

export interface CodexAgentClient {
  run(input: CodexAgentRunInput): Promise<CodexAgentRunResult>;
}

export interface CodexTaskRunnerOptions {
  command: string;
  workingDirectory: string;
  model?: string;
  sandbox: "read-only" | "workspace-write" | "danger-full-access";
  approvalPolicy: "untrusted" | "on-request" | "never" | "on-failure";
  timeoutMs: number;
  outputDirectory: string;
}

export class CodexTaskRunner implements TaskRunner {
  private readonly activeRuns = new Map<string, AbortController>();

  constructor(
    private readonly taskRepository: TaskRepository,
    private readonly eventLogService: EventLogService,
    private readonly codexAgentClient: CodexAgentClient,
    private readonly options: CodexTaskRunnerOptions
  ) {}

  async run(task: Task, observer?: TaskRunnerObserver): Promise<Task | undefined> {
    const abortController = new AbortController();
    this.activeRuns.set(task.id, abortController);
    const runningSummary = `开始调用 Codex 子任务：${task.title}`;
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.running",
      summary: runningSummary
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
    const outputFile = join(this.options.outputDirectory, `${task.id}-last-message.md`);
    const prompt = buildCodexTaskPrompt(task);
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.agent.started",
      summary: `Codex 子任务已启动：${task.title}`,
      payload: {
        cwd: this.options.workingDirectory,
        outputFile,
        sandbox: this.options.sandbox
      }
    });

    try {
      const result = await this.codexAgentClient.run({
        taskId: task.id,
        prompt,
        cwd: this.options.workingDirectory,
        outputFile,
        command: this.options.command,
        model: this.options.model,
        sandbox: this.options.sandbox,
        approvalPolicy: this.options.approvalPolicy,
        timeoutMs: this.options.timeoutMs,
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
      const finalMessage = normalizeAgentFinalMessage(result.finalMessage);
      const completedSummary = `Codex 子任务完成：\n${finalMessage}`;
      this.taskRepository.appendEvent({
        taskId: task.id,
        eventType: "task.completed",
        summary: completedSummary,
        payload: {
          outputFile,
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
          status: completed.status
        });
      }

      return completed;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const currentTask = this.taskRepository.getById(task.id);

      if (currentTask?.status === "interrupted") {
        this.taskRepository.appendEvent({
          taskId: task.id,
          eventType: "task.agent.cancelled",
          summary: `Codex 子任务已停止：${message}`
        });
        return currentTask;
      }

      const failedSummary = `Codex 子任务失败：${message}`;
      this.taskRepository.appendEvent({
        taskId: task.id,
        eventType: "task.failed",
        summary: failedSummary
      });
      const failed = this.taskRepository.updateStatus({
        taskId: task.id,
        status: "failed",
        latestProgress: failedSummary
      });

      if (failed) {
        this.eventLogService.record("task.agent.failed", {
          profileId: failed.profileId,
          taskId: failed.id,
          title: failed.title,
          status: failed.status,
          reason: message
        });
      }

      return failed;
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
}

export class CliCodexAgentClient implements CodexAgentClient {
  async run(input: CodexAgentRunInput): Promise<CodexAgentRunResult> {
    return new Promise((resolve, reject) => {
      const args = buildCodexExecArgs(input);
      const child = spawn(input.command, args, {
        cwd: input.cwd,
        env: {
          ...process.env,
          AURABOT_CHILD_TASK_ID: input.taskId
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
        stdoutLineBuffer = consumeCodexJsonlProgress(stdoutLineBuffer + text, input.onProgress);
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
          reject(new Error(`codex task timed out after ${input.timeoutMs}ms`));
          return;
        }

        if (cancelled) {
          reject(new Error("codex task cancelled"));
          return;
        }

        if (code !== 0) {
          reject(new Error(`codex exited ${code ?? `with signal ${signal ?? "unknown"}`}: ${stderr.trim()}`.trim()));
          return;
        }

        const finalMessage = existsSync(input.outputFile)
          ? readFileSync(input.outputFile, "utf8").trim()
          : stdout.trim();

        resolve({
          finalMessage,
          stdout,
          stderr
        });
      });
      child.stdin.end(input.prompt);
    });
  }
}

function buildCodexExecArgs(input: CodexAgentRunInput): string[] {
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

  args.push("-");

  return args;
}

function buildCodexTaskPrompt(task: Task): string {
  return [
    "你是 AuraBot 派出的子任务 Codex agent。",
    "你运行在独立任务线程中，主聊天线程会等待你的最终结果并回流给用户。",
    "请只处理下面这个任务，不要修改与任务无关的文件。",
    "如果需要读取仓库、调用 git/gh、生成报告或写入 artifact，可以在当前工作目录内完成。",
    "最终回复请使用中文，给出用户可以直接阅读的结果；如果创建或修改了文件，请列出路径。",
    "",
    `任务标题：${task.title}`,
    `任务 ID：${task.id}`
  ].join("\n");
}

function consumeCodexJsonlProgress(
  value: string,
  onProgress: ((progress: CodexAgentProgress) => void) | undefined
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

function mapCodexJsonlEventToProgress(line: string): CodexAgentProgress | undefined {
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
      const itemType = typeof item.type === "string" ? item.type : undefined;

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

function normalizeAgentFinalMessage(value: string): string {
  const trimmed = value.trim();

  return trimmed || "Codex 子任务完成，但没有返回最终文本。";
}
