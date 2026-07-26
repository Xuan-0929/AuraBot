"""
提供 Harness 工具调用的运行时上下文 adaptor 及其 factory registry。

ToolExecutionPipeline 根据 Tool 元数据从 AdaptorRegistry 创建 task-local adaptor，
依次注入文件系统或 Plan 上下文；本包不解析 LLM 协议，也不执行最终工具调用。
"""

from .base import Adaptor, AdaptorRouteError
from .file_system import (
    FileSystemAdaptor,
    PathBuildError,
    PathOutOfSandboxError,
)
from .plan_context import PlanContextAdaptor
from .registry import AdaptorFactory, AdaptorRegistry


__all__ = [
    "Adaptor",
    "AdaptorFactory",
    "AdaptorRegistry",
    "AdaptorRouteError",
    "FileSystemAdaptor",
    "PathBuildError",
    "PathOutOfSandboxError",
    "PlanContextAdaptor",
]
