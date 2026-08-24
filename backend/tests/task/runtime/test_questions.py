"""验证 Pi Extension UI payload 到 AuraBot Task Question 的纯解析边界。

测试使用手写 payload 固定 marker、方法、问题文本与 options 语义；SQLite、RPC 发送和
Task 状态转换由各自的集成测试负责，避免把外部 I/O 混进纯协议映射。
"""

from __future__ import annotations

import unittest

from backend.task.runtime.questions import QUESTION_MARKER, parse_question_request


class RuntimeQuestionTests(unittest.TestCase):
    def test_parses_marker_prefixed_select_question(self) -> None:
        parsed = parse_question_request(
            {
                "id": "request-1",
                "method": "select",
                "title": f"{QUESTION_MARKER}Choose a color",
                "options": ["blue", 17, "green"],
            }
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.request_id, "request-1")
        self.assertEqual(parsed.question, "Choose a color")
        self.assertEqual(parsed.options, ("blue", "green"))

    def test_rejects_non_aurabot_dialog(self) -> None:
        self.assertIsNone(
            parse_question_request(
                {
                    "id": "request-2",
                    "method": "input",
                    "title": "Third-party dialog",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
