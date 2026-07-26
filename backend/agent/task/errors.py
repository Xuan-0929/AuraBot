"""Task-manager admission errors."""


class DuplicateTaskNameError(ValueError):
    """Raised while a live or retained Task still owns a public name."""


class TaskManagerClosedError(RuntimeError):
    """Raised when admission is attempted during application shutdown."""


__all__ = ["DuplicateTaskNameError", "TaskManagerClosedError"]
