"""
提供建立在 Harness core 之上的具体 agent 执行策略。

HarnessFactory 根据 TaskType 选择 ReActHarness 或 PlanAndSolveHarness，策略复用公共
工具管线与 prompting 数据推进 Task；本包不注册 runtime，也不实现 Plan 聚合约束。
"""

from .plan_and_solve import PlanAndSolveHarness
from .react import ReActHarness


__all__ = ["PlanAndSolveHarness", "ReActHarness"]
