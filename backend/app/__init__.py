"""
定义 AuraBot health-only Backend 的应用包边界。

backend.main 从 gateway 子包挂载进程健康路由；本包不创建 service graph、不持有
runtime，也不导出执行领域实现，Python Memory 继续作为独立顶层包保留。
"""
