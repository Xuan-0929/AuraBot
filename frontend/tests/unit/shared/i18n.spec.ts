import { describe, expect, it } from "vitest";
import { agentStatusLabel, t, taskProgressLabel, taskStatusLabel } from "../../../src/i18n";
import type { Task } from "@shared/schemas/task";

describe("i18n", () => {
  it("renders Japanese UI copy and language option labels", () => {
    expect(t("ja", "language")).toBe("言語");
    expect(t("ja", "japanese")).toBe("日本語");
    expect(t("ja", "consoleTitle")).toBe("AuraBot コンソール");
  });

  it("renders Japanese agent and task labels", () => {
    const queuedReminder: Task = {
      id: "task-ja",
      profileId: "profile-default",
      parentSessionId: "session-parent",
      sessionId: "session-main",
      title: "水を飲む",
      kind: "time_reminder",
      status: "queued",
      latestProgress: "",
      dueAt: "2026-06-11T12:30:00.000Z",
      createdAt: "2026-06-11T12:00:00.000Z",
      updatedAt: "2026-06-11T12:00:00.000Z"
    };

    expect(agentStatusLabel("ja", "thinking")).toBe("考え中");
    expect(taskStatusLabel("ja", "completed")).toBe("完了");
    expect(taskProgressLabel("ja", queuedReminder)).toContain("リマインド");
  });
});
