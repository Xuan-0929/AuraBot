from __future__ import annotations

import httpx
import pytest

from Plan.errors import PlanToolFailure
from Plan.tools import PlanToolHandlers


@pytest.mark.asyncio
async def test_handler_uses_fixed_endpoint_and_flat_body():
    requests = []

    async def handle(request: httpx.Request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "created": True,
                "current_step_id": "s1",
                "total_step_count": 1,
            },
        )

    handlers = PlanToolHandlers(
        6401,
        transport=httpx.MockTransport(handle),
    )
    result = await handlers.call(
        "create_plan",
        {
            "goal": "Ship",
            "steps": [
                {
                    "id": "s1",
                    "title": "First",
                    "description": "Do first",
                    "expected_output": "Done",
                }
            ],
        },
        "Unique task",
    )

    assert result["created"] is True
    assert len(requests) == 1
    assert str(requests[0].url) == "http://127.0.0.1:6401/tool/plan/create"
    assert requests[0].method == "POST"
    assert requests[0].read().decode() == (
        '{"task_name":"Unique task","goal":"Ship","steps":[{"id":"s1",'
        '"title":"First","description":"Do first","expected_output":"Done"}]}'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_plan"),
        (404, "task_not_found"),
        (404, "plan_not_found"),
        (404, "step_not_found"),
        (409, "invalid_task_type"),
        (409, "invalid_task_state"),
        (409, "plan_already_exists"),
        (409, "completed_step_immutable"),
        (409, "step_not_current"),
        (409, "step_already_completed"),
    ],
)
async def test_known_backend_errors_are_mapped_to_safe_tool_errors(status, code):
    async def handle(request):
        return httpx.Response(
            status,
            json={"error": {"code": code, "message": "backend detail"}},
        )

    handlers = PlanToolHandlers(
        6401,
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(PlanToolFailure) as raised:
        await handlers.call("read_plan", {}, "Task")
    assert raised.value.code == code
    assert "backend detail" not in raised.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": {"code": "unknown", "message": "x"}}),
        httpx.Response(
            404,
            json={"error": {"code": "invalid_plan", "message": "x"}},
        ),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_unknown_or_malformed_backend_responses_are_protocol_errors(
    response,
):
    handlers = PlanToolHandlers(
        6401,
        transport=httpx.MockTransport(lambda request: response),
    )
    with pytest.raises(PlanToolFailure) as raised:
        await handlers.call("read_plan", {}, "Task")
    assert raised.value.code == "backend_protocol_error"


@pytest.mark.asyncio
async def test_connection_and_timeout_errors_are_stable():
    async def unavailable(request):
        raise httpx.ConnectError("secret", request=request)

    async def timeout(request):
        raise httpx.ReadTimeout("secret", request=request)

    for transport, expected in (
        (httpx.MockTransport(unavailable), "backend_unavailable"),
        (httpx.MockTransport(timeout), "backend_timeout"),
    ):
        handlers = PlanToolHandlers(6401, transport=transport)
        with pytest.raises(PlanToolFailure) as raised:
            await handlers.call("read_plan", {}, "Task")
        assert raised.value.code == expected
        assert "secret" not in raised.value.message
