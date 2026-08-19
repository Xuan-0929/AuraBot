/**
 * MessageRepository 历史附件兼容测试：旧 metadata 只投影安全名称字段。
 * 新消息不再接受附件输入；历史绝对路径必须在进入 Renderer 契约前被剥离。
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("MessageRepository attachments", () => {
  it("reads historical attachment names without exposing stored paths", () => {
    const services = createTestServices("aurabot-message-attachments-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const session = services.sessionRepository.getOrCreateMainSession(profile.id);

    services.database.prepare(`
      insert into messages (
        id, session_id, profile_id, role, content, metadata_json, created_at
      ) values (?, ?, ?, 'assistant', ?, ?, ?)
    `).run(
      "historical-message",
      session.id,
      profile.id,
      "历史附件",
      JSON.stringify({
        attachments: [{
          id: "chat-file-0",
          kind: "file",
          purpose: "input",
          name: "report.md",
          path: "/Users/xuan/project/docs/report.md"
        }]
      }),
      "2026-08-19T00:00:00.000Z"
    );
    const messages = services.messageRepository.listBySession(session.id);

    expect(messages[0]?.attachments).toEqual([{
      id: "chat-file-0",
      kind: "file",
      purpose: "input",
      name: "report.md"
    }]);
    expect(messages[0]?.attachments?.[0]).not.toHaveProperty("path");
  });
});
