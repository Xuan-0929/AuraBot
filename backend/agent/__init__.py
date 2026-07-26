"""
定义 Backend agent 子系统的包边界，容纳领域对象、运行能力与外部集成。

app 组合层从其稳定子包导入 LLM、Task、Skill 和 Tool 能力；本包本身不创建
BackendRuntime、不启动网络服务，也不扩大各子域的公开导出面。
"""

from .llm_client import ChatCompletionChoice, LLMClient

__all__ = ["ChatCompletionChoice", "LLMClient"]
