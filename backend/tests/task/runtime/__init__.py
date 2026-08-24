"""收纳 Task Runtime 的调度、执行、Question、进程与 RPC 行为测试。

这里的测试只观察 Runtime 对 Store、Pi 进程和业务状态产生的真实效果，不依赖
Gateway，也不把内部 asyncio 字典或协程实现当作公共契约。
"""
