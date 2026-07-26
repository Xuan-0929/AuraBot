"""Backend process composition root."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from ..agent.LLMClient import LLMClient
from ..agent.capabilities.lease import CapabilitySession
from ..agent.skills.SkillGroupManager import SkillGroupManager
from ..agent.task.TaskManager import TaskManager
from ..agent.task.events import TaskEventBroker
from ..agent.task.harness.HarnessFactory import HarnessFactory
from ..agent.task.harness.adaptors.AdaptorRegistry import AdaptorRegistry
from ..agent.tools.ToolGroupManager import ToolGroupManager
from ..agent.tools.native.PlanToolGroup import (
    PLAN_TOOL_GROUP_NAME,
    create_plan_tool_group,
)
from .services.plan_tool_service import PlanToolApplicationService
from .services.task_service import TaskApplicationService


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    max_running: int = 8
    default_timeout_ms: int = 300_000
    max_timeout_ms: int = 1_800_000
    terminal_retention_seconds: float = 100.0
    sse_subscriber_queue_size: int = 256
    sse_keepalive_seconds: float = 15.0
    shutdown_grace_seconds: float = 5.0

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
    ) -> "RuntimeSettings":
        raw = dict(value or {})
        defaults = cls()
        settings = cls(
            max_running=raw.get("max_running", defaults.max_running),
            default_timeout_ms=raw.get(
                "default_timeout_ms",
                defaults.default_timeout_ms,
            ),
            max_timeout_ms=raw.get(
                "max_timeout_ms",
                defaults.max_timeout_ms,
            ),
            terminal_retention_seconds=raw.get(
                "terminal_retention_seconds",
                defaults.terminal_retention_seconds,
            ),
            sse_subscriber_queue_size=raw.get(
                "sse_subscriber_queue_size",
                defaults.sse_subscriber_queue_size,
            ),
            sse_keepalive_seconds=raw.get(
                "sse_keepalive_seconds",
                defaults.sse_keepalive_seconds,
            ),
            shutdown_grace_seconds=raw.get(
                "shutdown_grace_seconds",
                defaults.shutdown_grace_seconds,
            ),
        )
        if settings.sse_keepalive_seconds <= 0:
            raise ValueError("sse_keepalive_seconds must be positive")
        if settings.shutdown_grace_seconds < 0:
            raise ValueError("shutdown_grace_seconds must be non-negative")
        return settings


class BackendRuntime:
    """Own all process-scoped clients, managers and application services."""

    def __init__(
        self,
        *,
        task_llm_client: LLMClient,
        settings: RuntimeSettings,
        adaptor_registry: AdaptorRegistry,
        tool_group_manager: ToolGroupManager,
        skill_group_manager: SkillGroupManager,
        harness_factory: HarnessFactory,
        task_manager: TaskManager,
        task_service: TaskApplicationService,
        plan_tool_service: PlanToolApplicationService,
    ):
        self.task_llm_client = task_llm_client
        self.settings = settings
        self.adaptor_registry = adaptor_registry
        self.tool_group_manager = tool_group_manager
        self.skill_group_manager = skill_group_manager
        self.harness_factory = harness_factory
        self.task_manager = task_manager
        self.task_service = task_service
        self.plan_tool_service = plan_tool_service
        self._closed = False
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._capability_sessions: set[CapabilitySession] = set()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        task_llm_client: LLMClient | None = None,
    ) -> "BackendRuntime":
        settings = RuntimeSettings.from_mapping(config.get("task_runtime"))
        if task_llm_client is None:
            model_config = config.get("task_model")
            if not isinstance(model_config, Mapping):
                raise ValueError("task_model configuration must be a mapping")
            task_llm_client = LLMClient(
                base_url=model_config["base_url"],
                api_key=model_config["api_key"],
                model=model_config["model_name"],
                max_concurrency=settings.max_running,
            )
        server_url = config.get("local_mcp_server")
        if not isinstance(server_url, str) or not server_url:
            raise ValueError("local_mcp_server must be a non-empty string")

        adaptor_registry = AdaptorRegistry()
        tool_group_manager = ToolGroupManager(
            native_factories={
                PLAN_TOOL_GROUP_NAME: partial(
                    create_plan_tool_group,
                    server_url,
                )
            }
        )
        skill_group_manager = SkillGroupManager()
        harness_factory = HarnessFactory(
            task_llm_client=task_llm_client,
            adaptor_registry=adaptor_registry,
        )
        broker = TaskEventBroker(
            queue_size=settings.sse_subscriber_queue_size
        )
        task_manager = TaskManager(
            harness_factory=harness_factory,
            tool_group_manager=tool_group_manager,
            skill_group_manager=skill_group_manager,
            event_broker=broker,
            max_running=settings.max_running,
            default_timeout_ms=settings.default_timeout_ms,
            max_timeout_ms=settings.max_timeout_ms,
            retention_seconds=settings.terminal_retention_seconds,
        )
        return cls(
            task_llm_client=task_llm_client,
            settings=settings,
            adaptor_registry=adaptor_registry,
            tool_group_manager=tool_group_manager,
            skill_group_manager=skill_group_manager,
            harness_factory=harness_factory,
            task_manager=task_manager,
            task_service=TaskApplicationService(task_manager),
            plan_tool_service=PlanToolApplicationService(task_manager),
        )

    def bind_event_loop(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.task_manager.bind_event_loop(
            loop or asyncio.get_running_loop()
        )

    def new_capability_session(self) -> CapabilitySession:
        if self._closed or self._closing:
            raise RuntimeError("BackendRuntime is closed")
        session = CapabilitySession(
            self.tool_group_manager,
            self.skill_group_manager,
            on_close=self._capability_sessions.discard,
        )
        self._capability_sessions.add(session)
        return session

    async def aclose(self) -> None:
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(
                self._aclose_impl(),
                name="aurabot-backend-runtime-close",
            )
            self._close_task = close_task
        await asyncio.shield(close_task)

    async def _aclose_impl(self) -> None:
        self._closing = True
        first_error: BaseException | None = None
        try:
            await self.task_manager.aclose(
                grace_seconds=self.settings.shutdown_grace_seconds
            )
        except BaseException as exc:
            first_error = exc
        for session in tuple(self._capability_sessions):
            try:
                session.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        for manager in (
            self.tool_group_manager,
            self.skill_group_manager,
        ):
            try:
                manager.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        try:
            await self.task_llm_client.aclose()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        self._closed = True
        if first_error is not None:
            raise first_error


__all__ = ["BackendRuntime", "RuntimeSettings"]
