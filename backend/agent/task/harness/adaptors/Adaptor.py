"""Base contract for adapting resolved tool calls inside a Harness."""

from abc import ABC, abstractmethod

from ....tools.core.ToolCallEntity import ResolvedToolCall


class Adaptor(ABC):
    """Adapt one routed ResolvedToolCall before gateway execution."""

    @abstractmethod
    def adapt(self, entity: ResolvedToolCall) -> ResolvedToolCall:
        """Return the adapted resolved tool call."""

        raise NotImplementedError


class AdaptorRouteError(RuntimeError):
    """Raised when a Tool cannot be routed to its configured Adaptor."""


__all__ = ["Adaptor", "AdaptorRouteError"]
