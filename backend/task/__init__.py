"""提供 AuraBot 第二轮 Python Pi Task Control Plane 的公开包边界。

具体产品模型、存储、调度、RPC、进程监督与编排分别位于同包小模块；Gateway 只应
依赖 TaskManager，而不从本入口获得旧 Runtime 或兼容类型。
"""

from .models import CreateTaskCommand, TaskRecord, TaskStatus

__all__ = ["CreateTaskCommand", "TaskRecord", "TaskStatus"]
