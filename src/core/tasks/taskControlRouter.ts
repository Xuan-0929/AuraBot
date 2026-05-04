import type { Task, TaskStatus } from "../../shared/schemas/task";

export type TaskControlAction = "query" | "start" | "interrupt" | "revise";

export interface TaskControlRequest {
  action: TaskControlAction;
  targetHint?: string;
  revisedTitle?: string;
}

const START_PATTERN = /(开始做|开始执行|执行一下|执行这个任务|执行任务|去做|跑一下)/;
const QUERY_PATTERN = /(做到哪|进度|怎么样|状态)/;
const INTERRUPT_PATTERN = /(停掉|取消|打断|中止|暂停|先停)/;
const REVISE_PATTERN = /(改成|修改为|修改成|换成)/;
const ACTIVE_STATUSES = new Set<TaskStatus>(["queued", "running", "waiting"]);

export function parseTaskControlRequest(content: string): TaskControlRequest {
  const text = content.trim();
  const reviseMatch = REVISE_PATTERN.exec(text);

  if (reviseMatch) {
    const beforeAction = text.slice(0, reviseMatch.index);
    const afterAction = text.slice(reviseMatch.index + reviseMatch[0].length);

    return {
      action: "revise",
      targetHint: normalizeTaskTargetHint(beforeAction),
      revisedTitle: normalizeRevisedTaskTitle(afterAction) ?? text
    };
  }

  const startMatch = START_PATTERN.exec(text);
  if (startMatch) {
    return {
      action: "start",
      targetHint: normalizeTaskTargetHint(text.replace(START_PATTERN, ""))
    };
  }

  const queryMatch = QUERY_PATTERN.exec(text);
  if (queryMatch) {
    return {
      action: "query",
      targetHint: normalizeTaskTargetHint(text.slice(0, queryMatch.index))
    };
  }

  const interruptMatch = INTERRUPT_PATTERN.exec(text);
  if (interruptMatch) {
    return {
      action: "interrupt",
      targetHint: normalizeTaskTargetHint(text.replace(INTERRUPT_PATTERN, ""))
    };
  }

  return {
    action: "revise",
    revisedTitle: text
  };
}

export function isTaskActive(task: Task): boolean {
  return ACTIVE_STATUSES.has(task.status);
}

export function selectTaskForControl({
  tasks,
  targetHint,
  activeOnly = false
}: {
  tasks: Task[];
  targetHint?: string;
  activeOnly?: boolean;
}): Task | undefined {
  const candidates = activeOnly ? tasks.filter(isTaskActive) : tasks;

  if (!targetHint) {
    return candidates[0];
  }

  return candidates
    .map((task, index) => ({
      task,
      score: scoreTaskMatch(task, targetHint) - index * 0.01
    }))
    .filter((candidate) => candidate.score > 0)
    .sort((left, right) => right.score - left.score)[0]?.task;
}

function scoreTaskMatch(task: Task, targetHint: string): number {
  const title = normalizeForMatch(task.title);
  const hint = normalizeForMatch(targetHint);

  if (!hint) {
    return 0;
  }

  if (title.includes(hint)) {
    return 100 + hint.length;
  }

  const hintChars = new Set([...hint]);
  let overlap = 0;

  for (const char of hintChars) {
    if (title.includes(char)) {
      overlap += 1;
    }
  }

  return overlap >= Math.min(2, hintChars.size) ? overlap : 0;
}

function normalizeTaskTargetHint(value: string): string | undefined {
  const hint = value
    .replace(/^[把将请麻烦先\s]+/, "")
    .replace(/(这个|那个|它|该)/g, "")
    .replace(/(任务|事项|一下|先|吧|呢|吗|么)/g, "")
    .replace(/[，。！？!?、\s]/g, "")
    .trim();

  return hint.length >= 2 ? hint : undefined;
}

function normalizeRevisedTaskTitle(value: string): string | undefined {
  const title = value
    .replace(/^[把将请麻烦先\s]+/, "")
    .replace(/\s+/g, "")
    .trim();

  return title.length > 0 ? title : undefined;
}

function normalizeForMatch(value: string): string {
  return value.replace(/[，。！？!?、\s]/g, "").toLowerCase();
}
