/**
 * 验证 Main Agent clause 到 Electron 消息历史的单向投影：本地消息可展示，但语义与
 * Memory 所有权不回落到旧 ChatOrchestrator/MemoryService，且广播发生在持久化之后。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MAIN_AGENT_UNAVAILABLE_MESSAGE,
  MainAgentChatProjection
} from "../../../src/main/mainAgentChatProjection";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("MainAgentChatProjection", () => {
  it("persists the user input and folds ordered clauses into one assistant message per input", async () => {
    const services = createTestServices("aurabot-main-agent-projection-");
    const submitText = vi.fn(async () => undefined);
    const created = vi.fn((message) => {
      const persisted = services.messageRepository
        .listBySession(message.sessionId)
        .find((candidate) => candidate.id === message.id);
      expect(persisted).toEqual(message);
    });
    const updated = vi.fn();
    const projection = new MainAgentChatProjection({
      profileService: services.profileService,
      sessionRepository: services.sessionRepository,
      messageRepository: services.messageRepository,
      eventLogService: services.eventLogService,
      realtimeClient: { submitText },
      onAssistantMessageCreated: created,
      onAssistantMessageUpdated: updated
    });

    const result = await projection.sendMessage({ content: "Remember oolong." });

    expect(result).toMatchObject({ agentStatus: "thinking" });
    expect(result.userMessage.content).toBe("Remember oolong.");
    expect(submitText).toHaveBeenCalledWith("Remember oolong.");

    await projection.deliverClause({
      clauseId: "clause-1",
      inputId: "input-1",
      invokeId: "invoke-1",
      revision: 1,
      text: "记住了。"
    });
    await projection.deliverClause({
      clauseId: "clause-2",
      inputId: "input-1",
      invokeId: "invoke-1",
      revision: 1,
      text: "你喜欢乌龙茶。"
    });
    await projection.deliverClause({
      clauseId: "clause-2",
      inputId: "input-1",
      invokeId: "invoke-1",
      revision: 1,
      text: "你喜欢乌龙茶。"
    });

    const messages = services.messageRepository.listBySession(result.userMessage.sessionId);
    expect(messages.map((message) => [message.role, message.content])).toEqual([
      ["user", "Remember oolong."],
      ["assistant", "记住了。你喜欢乌龙茶。"]
    ]);
    expect(created).toHaveBeenCalledTimes(1);
    expect(updated).toHaveBeenCalledTimes(1);
    expect(updated.mock.calls[0]?.[0].content).toBe("记住了。你喜欢乌龙茶。");
  });

  it("projects a stable unavailable message without invoking legacy Chat or Memory", async () => {
    const services = createTestServices("aurabot-main-agent-unavailable-");
    const legacyChat = vi.spyOn(services.chatOrchestrator, "sendMessage");
    const legacyMemory = vi.spyOn(services.memoryService, "enqueueWrite");
    const created = vi.fn();
    const projection = new MainAgentChatProjection({
      profileService: services.profileService,
      sessionRepository: services.sessionRepository,
      messageRepository: services.messageRepository,
      eventLogService: services.eventLogService,
      realtimeClient: { submitText: vi.fn(async () => { throw new Error("offline"); }) },
      onAssistantMessageCreated: created,
      onAssistantMessageUpdated: vi.fn()
    });

    const result = await projection.sendMessage({ content: "hello" });

    expect(result.agentStatus).toBe("idle");
    expect(result.assistantMessage?.content).toBe(MAIN_AGENT_UNAVAILABLE_MESSAGE);
    expect(created).toHaveBeenCalledWith(result.assistantMessage);
    expect(legacyChat).not.toHaveBeenCalled();
    expect(legacyMemory).not.toHaveBeenCalled();
    expect(services.memoryService.retrieveForProfile(result.userMessage.profileId).nodes).toEqual([]);
  });
});
