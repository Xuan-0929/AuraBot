/**
 * 提供无外部模型时的确定性 Gate 分类与回复时机判断。
 *
 * Gate 只输出 chat 或最小 task 意图；所有创建、提醒和控制类 Task 表达都进入统一
 * unavailable 分支，Memory 决策只对普通 chat 生效。
 */

import type {
  GateDecision,
  InputKind,
  MemoryDecision,
  ReplyDecision
} from "@shared/contracts/chat";
import type { MessageRole } from "@shared/schemas/message";

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
const TASK_REQUEST_PATTERN =
  /^(帮我|给我|替我|请你|麻烦|整理|生成|查一下|搜索|提醒我)|(做|写|撰写|整理|生成)(一份|一个|一篇|一版|份|个|篇|下|一下)|(任务|提醒|这个|它).*(进度|状态|停掉|取消|打断|中止|暂停|改成|推迟|调整|继续|重试|重新|打开|查看|删除)/;
const FRAGMENT_TRAILING_PATTERN = /(然后|但是|可是|因为|所以|我觉得|我想|就是|还有|以及|不过)$/;
const MEMORY_HINT_PATTERN = /(喜欢|讨厌|下周|明天|后天|今晚|别忘|记得|考试|面试|演唱会)/;
const DIRECT_CHAT_REPLY_PATTERN =
  /(在吗|在不在|在干嘛|干嘛呢|忙什么|说两句|说句话|说点|聊聊|陪我|理我|回我|吱声|冒个泡)/;

export function evaluateGate(input: GateEvaluationInput): GateDecision {
  const currentMessage = input.currentMessage.trim();
  const recentFragments = getRecentUserMessageContents(input);
  const coalescedText = [...recentFragments, currentMessage].join("\n");
  const inputKind: InputKind = TASK_REQUEST_PATTERN.test(currentMessage) ? "task" : "chat";
  const replyDecision = decideReply(currentMessage, coalescedText, inputKind);
  const memoryDecision = decideMemory(currentMessage, coalescedText, inputKind, replyDecision);
  const topicEnded =
    inputKind === "task" ||
    (replyDecision === "reply" &&
      (CLEAR_BOUNDARY_PATTERN.test(currentMessage) ||
        QUESTION_PATTERN.test(currentMessage) ||
        isDirectChatReplyPrompt(currentMessage) ||
        memoryDecision === "write_now"));

  return {
    replyDecision,
    inputKind,
    memoryDecision,
    confidence: inputKind === "task" ? 0.86 : replyDecision === "wait" ? 0.7 : 0.74,
    reason:
      inputKind === "task"
        ? "task-request: execution is temporarily unavailable"
        : replyDecision === "wait"
          ? "fragment: message lacks a clear semantic boundary"
          : memoryDecision === "write_now"
            ? "complete-chat: boundary reached and memory-worthy content detected"
            : "complete-chat: boundary reached with no memory-worthy content",
    coalescedText,
    topicEnded
  };
}

function getRecentUserMessageContents(input: GateEvaluationInput): string[] {
  const direct = input.recentUserMessages.map((message) => message.trim()).filter(Boolean);
  return direct.length > 0
    ? direct
    : (input.recentMessages ?? [])
        .filter((message) => message.role === "user")
        .map((message) => message.content.trim())
        .filter(Boolean);
}

function decideReply(
  message: string,
  coalescedText: string,
  inputKind: InputKind
): ReplyDecision {
  if (!message) {
    return "wait";
  }
  if (
    inputKind === "task" ||
    CLEAR_BOUNDARY_PATTERN.test(message) ||
    QUESTION_PATTERN.test(message) ||
    isDirectChatReplyPrompt(message)
  ) {
    return "reply";
  }
  if (FRAGMENT_TRAILING_PATTERN.test(message)) {
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
  if (inputKind === "task") {
    return "skip";
  }
  if (replyDecision === "wait") {
    return "defer";
  }
  return MEMORY_HINT_PATTERN.test(coalescedText) || MEMORY_HINT_PATTERN.test(message)
    ? "write_now"
    : "skip";
}

export function isDirectChatReplyPrompt(message: string): boolean {
  return DIRECT_CHAT_REPLY_PATTERN.test(message.trim());
}
