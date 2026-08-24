"""
提供与生产实现完全独立的可执行 Pi RPC 测试替身。

脚本只读取 stdin JSONL 并按 AURABOT_FAKE_PI_SCENARIO 输出预制 response/event；它不
import backend.task，因而协议测试不会与生产 parser、Supervisor 或状态机共同出错。
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time


SCENARIO = os.environ.get("AURABOT_FAKE_PI_SCENARIO", "complete")


def emit(value: object) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(command: dict[str, object], *, success: bool = True, **extra: object) -> None:
    emit(
        {
            "type": "response",
            "id": command.get("id"),
            "command": command.get("type"),
            "success": success,
            **extra,
        }
    )


if "--version" in sys.argv:
    print("0.84.2")
    raise SystemExit(0)

if SCENARIO == "ignore_terminate":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

for raw_line in sys.stdin.buffer:
    command = json.loads(raw_line)
    command_type = command.get("type")
    if command_type == "get_state":
        if SCENARIO == "startup_hang":
            continue
        response(
            command,
            data={
                "sessionId": next(
                    (
                        sys.argv[index + 1]
                        for index, value in enumerate(sys.argv[:-1])
                        if value == "--session-id"
                    ),
                    "unknown",
                ),
                "cwd": os.getcwd(),
                "piHome": os.environ.get("PI_CODING_AGENT_DIR"),
                "telemetry": os.environ.get("PI_TELEMETRY"),
                "versionCheck": os.environ.get("PI_SKIP_VERSION_CHECK"),
                "argv": sys.argv[1:],
            },
        )
    elif command_type == "prompt":
        expected_prompt = os.environ.get("AURABOT_FAKE_EXPECT_PROMPT_CONTAINS")
        if expected_prompt and expected_prompt not in str(command.get("message")):
            response(command, success=False, error="expected continuation missing")
        elif SCENARIO == "prompt_reject":
            response(command, success=False, error="prompt rejected")
        elif SCENARIO == "exit_zero_early":
            raise SystemExit(0)
        elif SCENARIO == "exit_nonzero":
            raise SystemExit(7)
        elif SCENARIO == "exit_nonzero_after_accept":
            response(command)
            raise SystemExit(7)
        elif SCENARIO == "malformed_json":
            response(command)
            sys.stdout.write("{bad json}\n")
            sys.stdout.flush()
        elif SCENARIO == "oversized_line":
            response(command)
            emit({"type": "agent_start", "padding": "x" * 4096})
        elif SCENARIO == "ask_main_agent":
            response(command)
            emit(
                {
                    "type": "extension_ui_request",
                    "id": "question-1",
                    "method": "input",
                    "title": "AURABOT_TASK_QUESTION_V1\nChoose a color",
                }
            )
        elif SCENARIO == "model_error":
            response(command)
            emit(
                {
                    "type": "agent_end",
                    "messages": [
                        {
                            "role": "assistant",
                            "stopReason": "error",
                            "errorMessage": "provider secret detail",
                        }
                    ],
                    "willRetry": False,
                }
            )
            emit({"type": "agent_settled"})
        elif SCENARIO == "extension_error":
            response(command)
            emit({"type": "extension_error", "error": "private extension detail"})
        elif SCENARIO == "running_hang":
            response(command)
        else:
            response(command)
            if SCENARIO == "stderr_flood":
                sys.stderr.write("x" * 200_000)
                sys.stderr.flush()
            emit(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "done",
                    },
                }
            )
            emit({"type": "agent_settled"})
    elif command_type == "get_last_assistant_text":
        response(
            command,
            data={"text": None if SCENARIO == "missing_result" else "final result"},
        )
    elif command_type == "abort":
        if SCENARIO not in {"ignore_abort", "ignore_terminate"}:
            response(command)
            emit({"type": "agent_settled"})
            raise SystemExit(0)
    elif command_type == "extension_ui_response":
        emit(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": f"answer:{command.get('value')}",
                },
            }
        )
        emit({"type": "agent_settled"})

if SCENARIO in {"ignore_abort", "ignore_terminate"}:
    while True:
        time.sleep(1)
