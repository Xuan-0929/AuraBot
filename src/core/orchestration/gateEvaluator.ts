import type { GateDecision, InputKind, MemoryDecision, ReplyDecision } from "../../shared/contracts/chat";
import type { MessageRole } from "../../shared/schemas/message";
import type { TaskKind } from "../../shared/schemas/task";
import { isReminderRequest } from "../tasks/reminderParser";

export interface GateContextMessage {
  role: MessageRole;
  content: string;
}

export interface GateEvaluationInput {
  currentMessage: string;
  recentUserMessages: string[];
  recentMessages?: GateContextMessage[];
}

const CLEAR_BOUNDARY_PATTERN = /[。！？!?]$/;
const QUESTION_PATTERN = /(吗|么|什么|咋|怎么|哪|多少|来着|？|\?)$/;
const ASSISTED_TASK_PATTERN = /^(帮我|给我|替我|请你|麻烦|整理|生成|查一下|搜索|提醒我)/;
const DIRECT_CREATION_TASK_PATTERN = /^(做|写|撰写|整理|生成)(一份|一个|一篇|一版|份|个|篇|下|一下)/;
const TASK_CONTROL_PATTERN = /(任务|这个|它).*(做到哪|进度|怎么样|状态|停掉|取消|打断|中止|暂停|改成|修改|换成)|(开始做|开始执行|执行一下|执行这个任务|执行任务|去做|跑一下).*(任务|这个|它)?|^(做到哪|进度|停掉|取消|打断|中止|暂停|先停|改成|修改|换成)/;
const FRAGMENT_TRAILING_PATTERN = /(然后|但是|可是|因为|所以|我觉得|我想|就是|还有|以及|不过)$/;
const MEMORY_HINT_PATTERN = /(喜欢|讨厌|下周|明天|后天|今晚|提醒|别忘|记得|考试|面试|演唱会)/;
const DIRECT_CHAT_REPLY_PATTERN = /(在吗|在不在|在干嘛|干嘛呢|忙什么|说两句|说句话|说点|聊聊|陪我|理我|回我|吱声|冒个泡)/;

export function evaluateGate(input: GateEvaluationInput): GateDecision {
  const currentMessage = input.currentMessage.trim();
  const recentFragments = getRecentUserMessageContents(input);
  const coalescedText = [...recentFragments, currentMessage].join("\n");
  const inputKind = classifyInputKind(currentMessage);
  const taskKind = inputKind === "task" ? classifyTaskKind(currentMessage) : undefined;
  const replyDecision = decideReply(currentMessage, coalescedText, inputKind);
  const memoryDecision = decideMemory(currentMessage, coalescedText, inputKind, replyDecision);
  const topicEnded = decideTopicEnded(currentMessage, inputKind, replyDecision, memoryDecision);
  const confidence = estimateConfidence({ currentMessage, inputKind, replyDecision, memoryDecision });

  return {
    replyDecision,
    inputKind,
    memoryDecision,
    confidence,
    reason: explainDecision({ currentMessage, inputKind, replyDecision, memoryDecision, taskKind }),
    coalescedText,
    topicEnded,
    taskKind
  };
}

function getRecentUserMessageContents(input: GateEvaluationInput): string[] {
  const directMessages = input.recentUserMessages.map((message) => message.trim()).filter(Boolean);

  if (directMessages.length > 0) {
    return directMessages;
  }

  return (input.recentMessages ?? [])
    .filter((message) => message.role === "user")
    .map((message) => message.content.trim())
    .filter(Boolean);
}

function decideTopicEnded(
  message: string,
  inputKind: InputKind,
  replyDecision: ReplyDecision,
  memoryDecision: MemoryDecision
): boolean {
  if (inputKind === "task" || inputKind === "task_control") {
    return true;
  }

  if (replyDecision === "wait") {
    return false;
  }

  return (
    CLEAR_BOUNDARY_PATTERN.test(message) ||
    QUESTION_PATTERN.test(message) ||
    isDirectChatReplyPrompt(message) ||
    memoryDecision === "write_now"
  );
}

function classifyInputKind(message: string): InputKind {
  if (TASK_CONTROL_PATTERN.test(message)) {
    return "task_control";
  }

  if (isReminderRequest(message)) {
    return "task";
  }

  return ASSISTED_TASK_PATTERN.test(message) || DIRECT_CREATION_TASK_PATTERN.test(message) ? "task" : "chat";
}

function classifyTaskKind(message: string): TaskKind {
  return isReminderRequest(message) ? "time_reminder" : "result_output";
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

  if (isDirectChatReplyPrompt(message)) {
    return "reply";
  }

  if (FRAGMENT_TRAILING_PATTERN.test(message)) {
    return "wait";
  }

  return coalescedText.length >= 28 ? "reply" : "wait";
}

export function isDirectChatReplyPrompt(message: string): boolean {
  return DIRECT_CHAT_REPLY_PATTERN.test(message.trim());
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
  taskKind?: TaskKind;
}): string {
  if (input.inputKind === "task") {
    return input.taskKind === "time_reminder"
      ? "task-request: scheduled reminder"
      : "task-request: result output request";
  }

  if (input.inputKind === "task_control") {
    return "task-control: main chat command about an existing task";
  }

  if (input.replyDecision === "wait") {
    return "fragment: message lacks a clear semantic boundary";
  }

  if (isDirectChatReplyPrompt(input.currentMessage)) {
    return "complete-chat: direct social prompt";
  }

  if (input.memoryDecision === "write_now") {
    return "complete-chat: boundary reached and memory-worthy content detected";
  }

  return "complete-chat: boundary reached with no memory-worthy content";
}
