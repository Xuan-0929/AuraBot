from enum import Enum


class TaskType(Enum):
    ReAct = "ReAct"
    PlanAndSolve = "PlanAndSolve"
    Goal = "Goal"  # 这一版不做它


class TaskState(Enum):
    Initializing = "Initializing"  # 任务正在初始化中
    Thinking = "Thinking"  # 正在等待推理服务返回
    Pushing = "Pushing"  # 正在解析、执行并包装模型返回
    Planning = "Planning"  # 仅P&S任务
    Concluding = "Concluding"  # 整理这一阶段的产物中
    Waiting = "Waiting"  # 等待主Agent的下一步指令或者用户输入
    Completed = "Completed"  # 完成了
    Failed = "Failed"  # 失败了
    Interrupted = "Interrupted"  # 被打断


__all__ = [
    "TaskState",
    "TaskType",
]
