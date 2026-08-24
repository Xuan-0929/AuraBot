"""收纳 Task SQLite、Question、Event、schema 与 session durability 测试。

测试统一使用真实临时 SQLite 和受控临时目录，确保模块拆分后仍由一条 connection 与
同一事务保存 Task 状态、问题和业务事件，并保持现有文件系统恢复语义。
"""
