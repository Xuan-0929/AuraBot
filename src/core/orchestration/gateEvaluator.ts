import type { GateDecision, InputKind, MemoryDecision, ReplyDecision } from "../../shared/contracts/chat";

export interface GateEvaluationInput {
  currentMessage: string;
  recentUserMessages: string[];
}

const CLEAR_BOUNDARY_PATTERN = /[。！？!?]$/;
const QUESTION_PATTERN = /(吗|么|什么|咋|怎么|哪|多少|来着|？|\?)$/;
const TASK_PATTERN = /^(帮我|给我|替我|请你|麻烦|整理|生成|写一份|做一个|查一下|搜索|提醒我)/;
const TASK_CONTROL_PATTERN = /(任务|这个|它).*(做到哪|进度|怎么样|状态|停掉|取消|打断|中止|暂停|改成|修改|换成)|(开始做|开始执行|执行一下|执行这个任务|执行任务|去做|跑一下).*(任务|这个|它)?|^(做到哪|进度|停掉|取消|打断|中止|暂停|先停|改成|修改|换成)/;
const FRAGMENT_TRAILING_PATTERN = /(然后|但是|可是|因为|所以|我觉得|我想|就是|还有|以及|不过)$/;
const MEMORY_HINT_PATTERN = /(喜欢|讨厌|下周|明天|后天|今晚|提醒|别忘|记得|考试|面试|演唱会)/;

export function evaluateGate(input: GateEvaluationInput): GateDecision {
  const currentMessage = input.currentMessage.trim();
  const recentFragments = input.recentUserMessages.map((message) => message.trim()).filter(Boolean);
  const coalescedText = [...recentFragments, currentMessage].join("\n");
  const inputKind = classifyInputKind(currentMessage);
  const replyDecision = decideReply(currentMessage, coalescedText, inputKind);
  const memoryDecision = decideMemory(currentMessage, coalescedText, inputKind, replyDecision);
  const confidence = estimateConfidence({ currentMessage, inputKind, replyDecision, memoryDecision });

  return {
    replyDecision,
    inputKind,
    memoryDecision,
    confidence,
    reason: explainDecision({ currentMessage, inputKind, replyDecision, memoryDecision }),
    coalescedText
  };
}

function classifyInputKind(message: string): InputKind {
  if (TASK_CONTROL_PATTERN.test(message)) {
    return "task_control";
  }

  return TASK_PATTERN.test(message) ? "task" : "chat";
}

function decideReply(message: string, coalescedText: string, inputKind: InputKind): ReplyDecision {
  if (!message) {
    return "wait";
  }

  if (
    inputKind === "task" ||
    inputKind === "task_control" ||
    CLEAR_BOUNDARY_PATTERN.test(message) ||
    QUESTION_PATTERN.test(message)
  ) {
    return "reply";
  }

  if (message.length <= 8 || FRAGMENT_TRAILING_PATTERN.test(message)) {
    return "wait";
  }

  return coalescedText.length >= 28 ? "reply" : "wait";
}

function decideMemory(
  message: string,
  coalescedText: string,
  inputKind: InputKind,
  replyDecision: ReplyDecision
): MemoryDecision {
  if (inputKind === "task" || inputKind === "task_control") {
    return "skip";
  }

  if (replyDecision === "wait") {
    return "defer";
  }

  return MEMORY_HINT_PATTERN.test(coalescedText) || MEMORY_HINT_PATTERN.test(message) ? "write_now" : "skip";
}

function estimateConfidence(input: {
  currentMessage: string;
  inputKind: InputKind;
  replyDecision: ReplyDecision;
  memoryDecision: MemoryDecision;
}): number {
  if (input.inputKind === "task" || input.inputKind === "task_control") {
    return 0.86;
  }

  if (CLEAR_BOUNDARY_PATTERN.test(input.currentMessage) || QUESTION_PATTERN.test(input.currentMessage)) {
    return input.memoryDecision === "write_now" ? 0.78 : 0.74;
  }

  if (input.replyDecision === "wait") {
    return 0.7;
  }

  return 0.62;
}

function explainDecision(input: {
  currentMessage: string;
  inputKind: InputKind;
  replyDecision: ReplyDecision;
  memoryDecision: MemoryDecision;
}): string {
  if (input.inputKind === "task") {
    return "task-request: clear command-like phrasing";
  }

  if (input.inputKind === "task_control") {
    return "task-control: main chat command about an existing task";
  }

  if (input.replyDecision === "wait") {
    return "fragment: message lacks a clear semantic boundary";
  }

  if (input.memoryDecision === "write_now") {
    return "complete-chat: boundary reached and memory-worthy content detected";
  }

  return "complete-chat: boundary reached with no memory-worthy content";
}
