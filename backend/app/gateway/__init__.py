"""
标记 AuraBot Backend 的 HTTP gateway 包。

backend.main 从本包挂载独立健康路由与版本化 Task REST/SSE；Gateway 只做公共 DTO
映射，不拥有 Task 状态、SQLite、调度、Pi RPC 或 subprocess 生命周期。
"""
