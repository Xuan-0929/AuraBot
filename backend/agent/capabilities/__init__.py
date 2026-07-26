"""
提供 Task runtime 使用的共享能力租约与集中注册入口。

BackendRuntime 持有 registry，Task 启动时创建 CapabilitySession 并在保留期结束时关闭；
本包不定义 Skill/Tool 业务模型，也不负责 Harness 调度或应用生命周期。
"""

from .lease import CapabilitySession

__all__ = ["CapabilitySession"]
