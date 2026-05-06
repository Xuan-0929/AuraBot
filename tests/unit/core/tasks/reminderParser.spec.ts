import { describe, expect, it } from "vitest";
import { parseReminderRequest } from "../../../../src/core/tasks/reminderParser";

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
});
