"""
标记 AuraBot health-only Backend 的 HTTP gateway 包。

backend.main 只从本包挂载独立健康路由；该边界不再包含业务 models、application
services、runtime dependencies 或执行面错误映射。
"""
