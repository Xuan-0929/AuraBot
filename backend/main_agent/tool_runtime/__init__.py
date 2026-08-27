"""Expose the in-process Main Agent tool catalog, stream parser, and deterministic execution harness.

Scheduler code consumes these records to freeze provider schemas and fence invokes; later Observation,
Task, and speech modules plug concrete executors into ToolHarness without moving policy into adapters or
creating an external tool service.
"""

from .definitions import (
    EffectClass,
    EffectFence,
    InvokeMode,
    InvokeSettlement,
    ObservationOutcome,
    ResponseGrant,
    StreamedSpeechReceipt,
    ToolCall,
    ToolDefinition,
    ToolExecutorError,
    ToolReceipt,
    ToolSurface,
    ToolSurfacePolicy,
)
from .harness import ObservationLimiter, ToolHarness
from .stream import SayClause, SayTextStream, ToolCallAssembler, segment_say_text

__all__ = [
    "EffectClass", "EffectFence", "InvokeMode", "InvokeSettlement", "ObservationLimiter", "ObservationOutcome",
    "ResponseGrant", "SayClause", "SayTextStream", "StreamedSpeechReceipt", "ToolCall",
    "ToolCallAssembler", "ToolDefinition", "ToolExecutorError", "ToolHarness", "ToolReceipt", "ToolSurface",
    "ToolSurfacePolicy", "segment_say_text",
]
