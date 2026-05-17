import type { Task, TaskStatus } from "../../shared/schemas/task";

export type TaskControlAction =
  | "query"
  | "start"
  | "interrupt"
  | "revise"
  | "continue"
  | "retry"
  | "rerun"
  | "open_result"
  | "delete";

export interface TaskControlRequest {
  action: TaskControlAction;
  targetHint?: string;
  revisedTitle?: string;
}

const START_PATTERN = /(开始做|开始执行|执行一下|执行这个任务|执行任务|去做|跑一下)/;
const CONTINUE_PATTERN = /(继续做|继续执行|继续这个任务|继续任务|继续)/;
const RETRY_PATTERN = /(重试|再试一次|重新试一下)/;
const RERUN_PATTERN = /(重新运行|重新执行|重新跑|再跑一次|重新来)/;
const OPEN_RESULT_PATTERN = /(打开.*结果|查看.*结果|看.*结果|打开.*产物|查看.*产物|打开.*文件)/;
const DELETE_PATTERN = /(删除已完成|删掉已完成|删除完成|删掉完成|删除|删掉|移除)/;
const QUERY_PATTERN = /(做到哪|进度|怎么样|状态)/;
const INTERRUPT_PATTERN = /(停掉|取消|打断|中止|暂停|先停)/;
const REVISE_PATTERN = /(改成|修改为|修改成|换成|改到|推迟到|调整到)/;
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

  const continueMatch = CONTINUE_PATTERN.exec(text);
  if (continueMatch) {
    return {
      action: "continue",
      targetHint: normalizeTaskTargetHint(text.replace(CONTINUE_PATTERN, ""))
    };
  }

  const retryMatch = RETRY_PATTERN.exec(text);
  if (retryMatch) {
    return {
      action: "retry",
      targetHint: normalizeTaskTargetHint(text.replace(RETRY_PATTERN, ""))
    };
  }

  const rerunMatch = RERUN_PATTERN.exec(text);
  if (rerunMatch) {
    return {
      action: "rerun",
      targetHint: normalizeTaskTargetHint(text.replace(RERUN_PATTERN, ""))
    };
  }

  const openResultMatch = OPEN_RESULT_PATTERN.exec(text);
  if (openResultMatch) {
    return {
      action: "open_result",
      targetHint: normalizeTaskTargetHint(text.replace(/(打开|查看|看|结果|产物|文件)/g, ""))
    };
  }

  const deleteMatch = DELETE_PATTERN.exec(text);
  if (deleteMatch) {
    return {
      action: "delete",
      targetHint: normalizeTaskTargetHint(text.replace(DELETE_PATTERN, ""))
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
  return selectTaskForControlWithReason({ tasks, targetHint, activeOnly }).task;
}

export type TaskSelectionReason = "selected" | "not_found" | "ambiguous";

export interface TaskSelectionResult {
  reason: TaskSelectionReason;
  task?: Task;
  candidates: Task[];
}

export function selectTaskForControlWithReason({
  tasks,
  targetHint,
  activeOnly = false
}: {
  tasks: Task[];
  targetHint?: string;
  activeOnly?: boolean;
}): TaskSelectionResult {
  const candidates = activeOnly ? tasks.filter(isTaskActive) : tasks;

  if (!targetHint) {
    return candidates[0]
      ? {
          reason: "selected",
          task: candidates[0],
          candidates: [candidates[0]]
        }
      : {
          reason: "not_found",
          candidates: []
        };
  }

  const scored = candidates
    .map((task, index) => ({
      task,
      score: scoreTaskMatch(task, targetHint) - index * 0.01
    }))
    .filter((candidate) => candidate.score > 0)
    .sort((left, right) => right.score - left.score);

  if (scored.length === 0) {
    return {
      reason: "not_found",
      candidates: []
    };
  }

  const [best, second] = scored;
  if (second && best.score - second.score < 1) {
    return {
      reason: "ambiguous",
      task: undefined,
      candidates: scored.slice(0, 3).map((candidate) => candidate.task)
    };
  }

  return {
    reason: "selected",
    task: best.task,
    candidates: [best.task]
  };
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
    .replace(/^的/u, "")
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
