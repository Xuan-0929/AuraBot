"""组织 AuraBot Task 的 SQLite 与 session 文件系统持久化边界。

TaskStore 是调用方使用的单一 SQLite 门面；schema、Question、Event、codec 与 session
删除模块共享其事务语义，并且本包不会反向依赖 Runtime、TaskManager 或 Gateway。
"""
