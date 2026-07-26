"""
定义 AuraBot Backend 应用包的组合、service 与 gateway 边界。

main 入口从本包创建 BackendRuntime 并注册 HTTP routes，各子包再依赖 agent 稳定门面；
本模块不启动服务、不持有 runtime，也不导出领域内部实现。
"""
