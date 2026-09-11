/**
 * 验证保留 UI 的日语文案和非 Task Agent 状态标签。
 *
 * 测试不再构造 Task schema 或调用 Task 进度格式化。
 */

import { describe, expect, it } from "vitest";
import { agentStatusLabel, t, UI_TEXT_KEYS } from "../../../src/i18n";

describe("i18n", () => {
  it("renders Japanese UI and current agent status labels", () => {
    expect(t("ja", "language")).toBe("言語");
    expect(t("ja", "consoleTitle")).toBe("AuraBot コンソール");
    expect(agentStatusLabel("ja", "thinking")).toBe("考え中");
  });

  it("uses Chinese as the fallback and localizes task labels", () => {
    expect(t(undefined, "language")).toBe("语言");
    expect(t("en", "taskQueued")).toBe("Queued");
    expect(t("ja", "taskQueued")).toBe("待機中");
  });

  it("defines every fixed UI key in each supported language", () => {
    for (const language of ["zh", "en", "ja"] as const) {
      for (const key of UI_TEXT_KEYS) {
        expect(t(language, key)).toEqual(expect.any(String));
        expect(t(language, key)).not.toBe("");
      }
    }
  });
});
