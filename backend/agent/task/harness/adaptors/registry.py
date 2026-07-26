"""
负责把 Tool 元数据中的 adaptor 名称解析为 Task-local adaptor 实例。

Harness 的工具执行管线通过不可变 factory registry 创建文件系统与 Plan 上下文 adaptor；
本模块依赖 Task 领域实体，但不执行工具调用，也不管理 Task runtime。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from ...domain import Task
from .base import Adaptor, AdaptorRouteError
from .file_system import FileSystemAdaptor
from .plan_context import PlanContextAdaptor


AdaptorFactory = Callable[[Task], Adaptor]


class AdaptorRegistry:
    """Immutable registry of named factories for Task-local Adaptors."""

    DEFAULT_FACTORIES: Mapping[str, AdaptorFactory] = {
        "FileSystemAdaptor": lambda task: FileSystemAdaptor(task.workdir),
        "PlanContextAdaptor": lambda task: PlanContextAdaptor(task),
    }

    def __init__(
        self,
        factories: Mapping[str, AdaptorFactory] | None = None,
    ):
        configured_factories = dict(self.DEFAULT_FACTORIES)
        if factories is not None:
            configured_factories.update(factories)
        if any(
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or not callable(factory)
            for name, factory in configured_factories.items()
        ):
            raise TypeError(
                "factories must map non-empty names to callables"
            )
        self._factories = MappingProxyType(configured_factories)

    @property
    def factories(self) -> Mapping[str, AdaptorFactory]:
        return self._factories

    def create(self, name: str, task: Task) -> Adaptor:
        """Create one Adaptor for the supplied Task."""

        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise AdaptorRouteError(
                f"no Adaptor factory is registered for {name!r}"
            ) from exc

        adaptor = factory(task)
        if not isinstance(adaptor, Adaptor):
            raise TypeError(
                f"Adaptor factory {name!r} must return Adaptor"
            )
        return adaptor

    def create_many(
        self,
        names: Sequence[str],
        task: Task,
    ) -> dict[str, Adaptor]:
        """Create one Task-local instance per distinct name, in first-use order."""

        instances: dict[str, Adaptor] = {}
        for name in names:
            if name not in instances:
                instances[name] = self.create(name, task)
        return instances


__all__ = ["AdaptorFactory", "AdaptorRegistry"]
