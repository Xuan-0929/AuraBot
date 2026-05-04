import { describe, expect, test } from "vitest";
import { extractSpeechTranscript } from "../../../src/renderer/src/features/pet/speechRecognition";

describe("extractSpeechTranscript", () => {
  test("joins transcript chunks from multiple speech recognition results", () => {
    expect(
      extractSpeechTranscript({
        results: {
          length: 2,
          0: {
            length: 1,
            0: { transcript: "帮我" }
          },
          1: {
            length: 2,
            0: { transcript: "整理" },
            1: { transcript: "会议清单" }
          }
        }
      })
    ).toBe("帮我整理会议清单");
  });
});
