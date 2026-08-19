/**
 * 验证保留 UI 的日语文案和非 Task Agent 状态标签。
 *
 * 测试不再构造 Task schema 或调用 Task 进度格式化。
 */

import { describe, expect, it } from "vitest";
import { agentStatusLabel, t } from "../../../src/i18n";

describe("i18n", () => {
  it("renders Japanese UI and current agent status labels", () => {
    expect(t("ja", "language")).toBe("言語");
    expect(t("ja", "consoleTitle")).toBe("AuraBot コンソール");
    expect(agentStatusLabel("ja", "thinking")).toBe("考え中");
  });
});
