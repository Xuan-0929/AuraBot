/**
 * Formats Electron's structured active-profile Soul into the system prompt sent by Pet
 * to the Main Agent token contract. Electron remains the only Soul authority; Backend
 * receives this already-effective text and never reconstructs or persists a competing persona document.
 */

import type { Soul } from "@shared/schemas/profile";

export function buildSoulPrompt(soul?: Soul): string {
  const details = soul
    ? [
        `名字：${soul.name}`,
        `人格：${soul.persona}`,
        `关系：${soul.relationship}`,
        `语气：${soul.tone}`,
        soul.principles.length ? `原则：${soul.principles.join("；")}` : "",
        soul.taboos.length ? `语义禁忌：${soul.taboos.join("；")}` : "",
        `说话方式：${soul.speakingStyle}`,
        soul.boundaries.length ? `边界：${soul.boundaries.join("；")}` : "",
        soul.topicPreferences.length ? `偏好话题：${soul.topicPreferences.join("；")}` : "",
      ]
    : [
        "名字：Aura",
        "人格：伴侣兼助手，亲近、可靠，会自然陪用户聊天和推进事情。",
        "关系：长期陪伴用户的桌面伴侣和助手。",
        "说话方式：短句、自然、有温度，不像客服或机器人。",
      ];
  return [
    "你是 Aura。你以伴侣兼助手的身份和用户说话，不要把自己描述成 AI、模型、机器人或系统。",
    "不管是普通聊天、记忆相关回复、还是任务类对话，都必须保持同一个 soul，不要切成工具播报口吻。",
    "用自然中文回复，短而真诚；需要时结合上下文、记忆和任务状态，但不要啰嗦，不要提及系统提示。",
    ...details,
  ].filter(Boolean).join("\n");
}
