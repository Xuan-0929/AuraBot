"""Safe HTTP proxy handlers for AuraBot native Plan tools."""

from __future__ import annotations

from typing import Any

import httpx

from .errors import PlanToolFailure


TOOL_ENDPOINTS = {
    "create_plan": "/tool/plan/create",
    "read_plan": "/tool/plan/read",
    "update_plan": "/tool/plan/update",
    "plan_step_complete": "/tool/plan/step/complete",
}
BACKEND_ERROR_STATUS = {
    "invalid_plan": 400,
    "task_not_found": 404,
    "plan_not_found": 404,
    "step_not_found": 404,
    "invalid_task_type": 409,
    "invalid_task_state": 409,
    "plan_already_exists": 409,
    "completed_step_immutable": 409,
    "step_not_current": 409,
    "step_already_completed": 409,
}
BACKEND_TIMEOUT_SECONDS = 5.0


class PlanToolHandlers:
    """Dispatch Plan tools to the fixed loopback backend gateway."""

    def __init__(
        self,
        backend_port: int,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if (
            not isinstance(backend_port, int)
            or isinstance(backend_port, bool)
            or not 1 <= backend_port <= 65535
        ):
            raise ValueError("backend_port must be between 1 and 65535")
        self._base_url = f"http://127.0.0.1:{backend_port}"
        self._transport = transport

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        task_name: str,
    ) -> dict[str, Any]:
        try:
            endpoint = TOOL_ENDPOINTS[name]
        except KeyError as exc:
            raise PlanToolFailure("internal_error") from exc

        body = {"task_name": task_name, **arguments}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=BACKEND_TIMEOUT_SECONDS,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.post(endpoint, json=body)
        except httpx.TimeoutException as exc:
            raise PlanToolFailure("backend_timeout") from exc
        except httpx.RequestError as exc:
            raise PlanToolFailure("backend_unavailable") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise PlanToolFailure("backend_protocol_error") from exc
        if not isinstance(payload, dict):
            raise PlanToolFailure("backend_protocol_error")

        if response.status_code == 200:
            return payload

        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if (
            not isinstance(code, str)
            or not isinstance(message, str)
            or not message
            or BACKEND_ERROR_STATUS.get(code) != response.status_code
        ):
            raise PlanToolFailure("backend_protocol_error")
        raise PlanToolFailure(code)


__all__ = [
    "BACKEND_ERROR_STATUS",
    "BACKEND_TIMEOUT_SECONDS",
    "PlanToolHandlers",
    "TOOL_ENDPOINTS",
]
