import { basename, isAbsolute, resolve } from "node:path";
import type { MessageAttachment } from "@shared/schemas/message";
import type { Soul } from "@shared/schemas/profile";
import type { Task, TaskEvent } from "@shared/schemas/task";
import type { TaskArtifact } from "../storage/repositories/taskArtifactRepository";
import type { ResponseScene, VoiceAdapter } from "../voice/voiceAdapter";

export interface TaskMessageDraft {
  content: string;
  attachments: MessageAttachment[];
}

export interface TaskVoiceOptions {
  soul?: Soul;
  voiceAdapter: VoiceAdapter;
}

export function buildTaskSettledMessageDraft(
  task: Task,
  events: TaskEvent[] = [],
  artifacts: TaskArtifact[] = []
): TaskMessageDraft {
  if (task.status === "completed") {
    const attachments = buildTaskResultAttachments(task.id, events, artifacts);

    if (attachments.length > 0) {
      return {
        content: `好啦，${describeTaskResultSubject(task)}写好了。文件我放在下面，你可以直接打开看，也可以去文件夹里找。`,
        attachments
      };
    }

    return {
      content: `好啦，${formatCompletedText(task, cleanTaskResultText(getLatestMeaningfulSummary(task, events)))}`,
      attachments: []
    };
  }

  if (task.status === "failed") {
    const failureDetail = cleanTaskResultText(getLatestMeaningfulSummary(task, events));

    return {
      content: failureDetail
        ? `这次我先卡在 ${failureDetail} 这一步了。你要是愿意，我们接着一起改。`
        : "这次我先卡住了一下。你要是愿意，我们接着一起改。",
      attachments: []
    };
  }

  if (task.status === "interrupted") {
    return {
      content: "我先在这里停一下。你想换个方向的话，直接跟我说新的要求就行。",
      attachments: []
    };
  }

  return {
    content: `这件事我已经在弄了，最近一步是：${ensureSentenceEnding(cleanTaskProgressText(task.latestProgress))}\n弄好后我会回来跟你说。`,
    attachments: []
  };
}

export async function buildTaskSettledMessageDraftWithVoice(
  task: Task,
  events: TaskEvent[] = [],
  artifactsOrOptions: TaskArtifact[] | TaskVoiceOptions,
  options?: TaskVoiceOptions
): Promise<TaskMessageDraft> {
  const artifacts = Array.isArray(artifactsOrOptions) ? artifactsOrOptions : [];
  const voiceOptions = Array.isArray(artifactsOrOptions) ? options : artifactsOrOptions;
  if (!voiceOptions) {
    throw new Error("Task voice options are required.");
  }
  const draft = buildTaskSettledMessageDraft(task, events, artifacts);
  const scene = getSettledTaskScene(task);
  const content = await voiceOptions.voiceAdapter.render({
    scene,
    channel: "chat",
    soul: voiceOptions.soul,
    facts: buildTaskVoiceFacts(task, events, draft.attachments)
  });

  return {
    ...draft,
    content
  };
}

export function buildTaskStatusResponse(task: Task, events: TaskEvent[] = []): string {
  if (task.status === "completed" || task.status === "failed" || task.status === "interrupted") {
    return buildTaskSettledMessageDraft(task, events).content;
  }

  if (task.status === "queued") {
    return `${formatTaskReference(task)}我先替你记在旁边了。你想让我现在开始的话，直接跟我说一声就行。`;
  }

  const latestSummary = cleanTaskProgressText(getLatestMeaningfulSummary(task, events));
  return `${formatTaskReference(task)}我已经在弄了，最近一步是：${ensureSentenceEnding(latestSummary)}\n弄好后我会回来跟你说。`;
}

export async function buildTaskStatusResponseWithVoice(
  task: Task,
  events: TaskEvent[] = [],
  options: TaskVoiceOptions,
  artifacts: TaskArtifact[] = []
): Promise<string> {
  return options.voiceAdapter.render({
    scene: task.status === "completed" || task.status === "failed" || task.status === "interrupted"
      ? getSettledTaskScene(task)
      : "task.status",
    channel: "chat",
    soul: options.soul,
    facts: buildTaskVoiceFacts(task, events, buildTaskResultAttachments(task.id, events, artifacts))
  });
}

export function buildTaskResultAttachments(
  taskId: string,
  events: TaskEvent[],
  artifacts: TaskArtifact[] = []
): MessageAttachment[] {
  return listTaskResultPaths(events, artifacts).map((path, index) => ({
    id: `${taskId}-file-${index}`,
    kind: "file",
    taskId,
    name: basename(path),
    path
  }));
}

export function isTaskResultPathAllowed(
  events: TaskEvent[],
  path: string,
  artifacts: TaskArtifact[] = []
): boolean {
  const normalizedPath = normalizeComparablePath(path);

  return listTaskResultPaths(events, artifacts).some((resultPath) => normalizeComparablePath(resultPath) === normalizedPath);
}

function listTaskResultPaths(events: TaskEvent[], artifacts: TaskArtifact[] = []): string[] {
  const paths = new Set<string>();
  const cwd = findTaskWorkingDirectory(events);

  for (const artifact of artifacts) {
    if (artifact.artifactType === "file") {
      paths.add(resolveTaskPath(artifact.uri, cwd));
    }
  }

  if (paths.size > 0) {
    return [...paths];
  }

  for (const event of events) {
    for (const path of getEventFileChangePaths(event)) {
      paths.add(resolveTaskPath(path, cwd));
    }
  }

  if (paths.size > 0) {
    return [...paths];
  }

  for (const event of events) {
    const outputFile = typeof event.payload.outputFile === "string" ? event.payload.outputFile : undefined;

    if (outputFile) {
      paths.add(resolveTaskPath(outputFile, cwd));
    }
  }

  return [...paths];
}

function getEventFileChangePaths(event: TaskEvent): string[] {
  const paths: string[] = [];
  const firstChange = isRecord(event.payload.fileChange) ? event.payload.fileChange : undefined;

  if (typeof firstChange?.path === "string") {
    paths.push(firstChange.path);
  }

  const fileChanges = Array.isArray(event.payload.fileChanges) ? event.payload.fileChanges : [];
  for (const change of fileChanges) {
    if (isRecord(change) && typeof change.path === "string") {
      paths.push(change.path);
    }
  }

  const changes = Array.isArray(event.payload.changes) ? event.payload.changes : [];
  for (const change of changes) {
    if (isRecord(change) && typeof change.path === "string") {
      paths.push(change.path);
    }
  }

  return paths;
}

function findTaskWorkingDirectory(events: TaskEvent[]): string | undefined {
  for (const event of events) {
    if (typeof event.payload.cwd === "string") {
      return event.payload.cwd;
    }
  }

  return undefined;
}

function resolveTaskPath(path: string, cwd: string | undefined): string {
  if (isAbsolute(path) || !cwd) {
    return path;
  }

  return resolve(cwd, path);
}

function normalizeComparablePath(path: string): string {
  return resolve(path);
}

function getLatestMeaningfulSummary(task: Task, events: TaskEvent[]): string {
  const completedEvent = [...events]
    .reverse()
    .find((event) =>
      ["task.completed", "task.failed", "task.agent.cancelled", "task.recovered_orphaned"].includes(
        event.eventType
      )
    );

  return completedEvent?.summary ?? task.latestProgress;
}

function cleanTaskResultText(value: string): string {
  return value
    .replace(/^(Codex|Claude Code|Hermes) 子任务完成：\s*/u, "")
    .replace(/^(Codex|Claude Code|Hermes) 子任务失败：\s*/u, "")
    .replace(/^任务执行异常：\s*/u, "")
    .replace(/^已完成：\s*/u, "")
    .replace(/^开始处理：\s*/u, "")
    .replace(/^(请|麻烦|帮我|帮我把)\s*/u, "")
    .trim();
}

function formatCompletedText(task: Task, resultText: string): string {
  if (/完成|好了|写好|整理好|准备好/u.test(resultText)) {
    return resultText;
  }

  const title = cleanTaskTitle(task.title);

  if (/清单/u.test(title)) {
    return `${title}整理好了。`;
  }

  if (/周报|报告|文档|总结|摘要/u.test(title)) {
    return `${title}写好了。`;
  }

  return `${title || "这件事"}处理好了。`;
}

function cleanTaskProgressText(value: string): string {
  const cleaned = cleanTaskResultText(value)
    .replace(/^(Codex|Claude Code|Hermes) 子任务已启动：.*$/u, "已经开始处理")
    .replace(/^开始调用 (Codex|Claude Code|Hermes) 子任务：.*$/u, "已经开始处理")
    .replace(/^(Codex|Claude Code|Hermes) 子任务开始处理任务。?$/u, "已经开始处理")
    .replace(/^(Codex|Claude Code|Hermes) 子任务本轮处理完成，正在收尾。?$/u, "正在收尾");

  return cleaned || "已经开始处理";
}

function cleanTaskTitle(value: string): string {
  return value
    .trim()
    .replace(/[。.!！]*$/u, "")
    .replace(/^(请|麻烦)\s*/u, "")
    .replace(
      /^(帮我把|帮我做一下|帮我写一下|帮我整理一下|帮我生成一下|帮我做|帮我写|帮我整理|帮我生成|帮我|做一下|写一下|整理一下|生成一下|做|写|整理|生成)\s*/u,
      ""
    )
    .replace(/^(一份|一个|一篇|一版|这份|这个|一下)\s*/u, "")
    .trim();
}

function formatTaskReference(task: Task): string {
  const title = cleanTaskTitle(task.title);

  if (!title) {
    return "这件事";
  }

  return `「${title}」这件事`;
}

function ensureSentenceEnding(value: string): string {
  const trimmed = value.trim();

  if (!trimmed) {
    return "已经开始处理。";
  }

  if (/[。！？!?…]$/u.test(trimmed)) {
    return trimmed;
  }

  return `${trimmed}。`;
}

function describeTaskResultSubject(task: Task): string {
  const title = cleanTaskTitle(task.title);

  if (/github\s*周报/i.test(title)) {
    return "GitHub 周报";
  }

  if (/周报/u.test(title)) {
    return "周报";
  }

  if (/报告|文档|清单|总结|摘要/u.test(title)) {
    return title;
  }

  return "结果";
}

function getSettledTaskScene(task: Task): ResponseScene {
  if (task.status === "failed") {
    return "task.failed";
  }

  if (task.status === "interrupted") {
    return "task.interrupted";
  }

  return "task.completed";
}

function buildTaskVoiceFacts(task: Task, events: TaskEvent[], attachments: MessageAttachment[]): Record<string, unknown> {
  const latestSummary = cleanTaskResultText(getLatestMeaningfulSummary(task, events));

  return {
    taskTitle: cleanTaskTitle(task.title),
    status: task.status,
    latestProgress: cleanTaskProgressText(getLatestMeaningfulSummary(task, events)),
    resultSummary: latestSummary,
    resultSubject: describeTaskResultSubject(task),
    hasAttachments: attachments.length > 0,
    attachmentNames: attachments.map((attachment) => attachment.name)
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
