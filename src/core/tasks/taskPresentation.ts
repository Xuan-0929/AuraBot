import { basename, isAbsolute, resolve } from "node:path";
import type { MessageAttachment } from "../../shared/schemas/message";
import type { Task, TaskEvent } from "../../shared/schemas/task";

export interface TaskMessageDraft {
  content: string;
  attachments: MessageAttachment[];
}

export function buildTaskSettledMessageDraft(task: Task, events: TaskEvent[] = []): TaskMessageDraft {
  if (task.status === "completed") {
    const attachments = buildTaskResultAttachments(task.id, events);

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
    return {
      content: `这次没跑成，卡在：${cleanTaskResultText(getLatestMeaningfulSummary(task, events))}。我们可以接着改。`,
      attachments: []
    };
  }

  if (task.status === "interrupted") {
    return {
      content: "我先停下来了。你想改方向的话，直接跟我说新的要求就行。",
      attachments: []
    };
  }

  return {
    content: `我还在处理，最近一步是：${cleanTaskResultText(task.latestProgress)}`,
    attachments: []
  };
}

export function buildTaskStatusResponse(task: Task, events: TaskEvent[] = []): string {
  if (task.status === "completed" || task.status === "failed" || task.status === "interrupted") {
    return buildTaskSettledMessageDraft(task, events).content;
  }

  if (task.status === "queued") {
    return `我还没开始处理「${cleanTaskTitle(task.title)}」。你想现在启动的话，直接说“开始做这个任务”。`;
  }

  const latestSummary = cleanTaskProgressText(getLatestMeaningfulSummary(task, events));
  return `我正在处理「${cleanTaskTitle(task.title)}」，最近一步是：${latestSummary}\n完成后我会回来告诉你。`;
}

export function buildTaskResultAttachments(taskId: string, events: TaskEvent[]): MessageAttachment[] {
  return listTaskResultPaths(events).map((path, index) => ({
    id: `${taskId}-file-${index}`,
    kind: "file",
    taskId,
    name: basename(path),
    path
  }));
}

export function isTaskResultPathAllowed(events: TaskEvent[], path: string): boolean {
  const normalizedPath = normalizeComparablePath(path);

  return listTaskResultPaths(events).some((resultPath) => normalizeComparablePath(resultPath) === normalizedPath);
}

function listTaskResultPaths(events: TaskEvent[]): string[] {
  const paths = new Set<string>();
  const cwd = findTaskWorkingDirectory(events);

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
    .replace(/^Codex 子任务完成：\s*/u, "")
    .replace(/^Codex 子任务失败：\s*/u, "")
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
    .replace(/^Codex 子任务已启动：.*$/u, "已经开始处理")
    .replace(/^开始调用 Codex 子任务：.*$/u, "已经开始处理")
    .replace(/^Codex 子任务开始处理任务。?$/u, "已经开始处理")
    .replace(/^Codex 子任务本轮处理完成，正在收尾。?$/u, "正在收尾");

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
