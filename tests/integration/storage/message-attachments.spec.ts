import { afterEach, describe, expect, it } from "vitest";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("MessageRepository attachments", () => {
  it("persists file attachments for task result messages", () => {
    const services = createTestServices("aurabot-message-attachments-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const session = services.sessionRepository.getOrCreateMainSession(profile.id);

    const created = services.messageRepository.create({
      sessionId: session.id,
      profileId: profile.id,
      role: "assistant",
      content: "好啦，GitHub 周报写好了。文件我放在下面，你可以直接打开看，也可以去文件夹里找。",
      attachments: [
        {
          id: "task-weekly-file-0",
          kind: "file",
          taskId: "task-weekly",
          name: "report.md",
          path: "/Users/xuan/project/docs/report.md"
        }
      ]
    });
    const messages = services.messageRepository.listBySession(session.id);

    expect(created.attachments).toHaveLength(1);
    expect(messages[0]?.attachments).toEqual(created.attachments);
  });
});
