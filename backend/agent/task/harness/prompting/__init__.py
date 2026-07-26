"""
组织 Harness 策略共享的静态提示词模板与 Task 上下文格式化函数。

strategies 先通过 context 生成稳定字段，再填充 templates 中的系统提示词；
本包只处理文本表示，不读取 LLM 响应、不执行工具，也不改变 Task 状态。
"""

from .context import build_common_prompt_values
from .templates import system_prompt_PlanAndSolve, system_prompt_ReAct


__all__ = [
    "build_common_prompt_values",
    "system_prompt_PlanAndSolve",
    "system_prompt_ReAct",
]
