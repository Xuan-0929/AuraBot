"""
封装 Task runtime 从准入、中断、保留到关闭的内部生命周期控制器。

TaskManager 直接组合各 controller，controller 共享 registry、scheduler、event publisher
和内部 record；本包不是公共 API，不从 task.runtime facade 导出具体控制器。
"""

__all__: list[str] = []
