"""组织 AuraBot Task 的进程内执行平面。

本包包含 FIFO 准入、单 Task 执行协程、Pi subprocess/RPC 与运行时协调状态；它可以
依赖 Task 模型和持久化门面，但不依赖 TaskManager、FastAPI 或 Gateway。
"""
