"""
定义 Harness 工具调用 adaptor 的最小契约与路由错误语义。

ToolExecutionPipeline 在协议解析后把 ResolvedToolCall 依次交给 Adaptor 实现，
再送往 gateway 执行；本模块不选择 adaptor、不解析工具协议，也不访问 Task runtime。
"""

from abc import ABC, abstractmethod

from ....tools.core.model import ResolvedToolCall


class Adaptor(ABC):
    """Adapt one routed ResolvedToolCall before gateway execution."""

    @abstractmethod
    def adapt(self, entity: ResolvedToolCall) -> ResolvedToolCall:
        """Return the adapted resolved tool call."""

        raise NotImplementedError


class AdaptorRouteError(RuntimeError):
    """Raised when a Tool cannot be routed to its configured Adaptor."""


__all__ = ["Adaptor", "AdaptorRouteError"]
