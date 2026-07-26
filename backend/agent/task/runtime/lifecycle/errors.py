"""
定义 TaskManager 准入阶段可由应用层稳定识别的错误语义。

admission controller 在名称冲突或 runtime 关闭时抛出这些异常，TaskManager 原样暴露，
上层 service 再转换为传输错误；本模块不执行校验、状态迁移或 HTTP 映射。
"""


class DuplicateTaskNameError(ValueError):
    """Raised while a live or retained Task still owns a public name."""


class TaskManagerClosedError(RuntimeError):
    """Raised when admission is attempted during application shutdown."""


__all__ = ["DuplicateTaskNameError", "TaskManagerClosedError"]
