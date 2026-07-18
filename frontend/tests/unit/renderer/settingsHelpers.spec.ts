import { describe, expect, it } from "vitest";
import { testConnectionAriaLabel } from "../../../src/features/settings/settingsHelpers";

describe("settings helpers", () => {
  it("localizes the model connection test label in Japanese", () => {
    expect(testConnectionAriaLabel("会話モデル", "ja")).toBe("会話モデルの接続をテスト");
  });
});
