import { describe, expect, test } from "vitest";
import { splitTextIntoSentences } from "@shared/text/sentences";

describe("splitTextIntoSentences", () => {
  test("splits on chat-style sentence ending punctuation", () => {
    expect(
      splitTextIntoSentences("好呀～我在这儿~继续说；慢慢来……别急〜尾巴", { flushRemainder: true }).sentences.map(
        (sentence) => sentence.text
      )
    ).toEqual(["好呀～", "我在这儿~", "继续说；", "慢慢来……", "别急〜", "尾巴"]);
  });
});
