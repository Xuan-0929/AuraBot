import { describe, expect, it } from "vitest";
import { parseReminderRequest } from "../../../../packages/core-legacy/src/tasks/reminderParser";

describe("parseReminderRequest", () => {
  it.each(["两分钟后提醒我喝水", "提醒我两分钟后喝水"])(
    "turns %s into scheduled reminder task metadata",
    (message) => {
      const reminder = parseReminderRequest({
        message,
        nowIso: "2026-05-06T12:00:00.000+08:00"
      });

      expect(reminder).toEqual({
        content: "喝水",
        dueAt: "2026-05-06T04:02:00.000Z"
      });
    }
  );

  it("parses absolute reminder time like tomorrow morning at nine", () => {
    const reminder = parseReminderRequest({
      message: "明天早上九点提醒我喝水",
      nowIso: "2026-05-16T20:00:00.000+08:00"
    });

    expect(reminder).toEqual({
      content: "喝水",
      dueAt: "2026-05-17T01:00:00.000Z"
    });
  });

  it("treats a vague later reminder as ten minutes later", () => {
    const reminder = parseReminderRequest({
      message: "稍后提醒我喝水",
      nowIso: "2026-05-16T20:00:00.000+08:00"
    });

    expect(reminder).toEqual({
      content: "喝水",
      dueAt: "2026-05-16T12:10:00.000Z"
    });
  });
});
