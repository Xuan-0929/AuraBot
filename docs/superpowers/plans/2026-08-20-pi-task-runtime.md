# AuraBot 第二轮轻量 Pi Task Runtime 实施计划

> 批准日期：2026-08-20。本文用于实施追踪；架构事实以
> [Pi Task Runtime](../../modules/task-agent.md) 为唯一来源。

## 目标

在现有 Python Backend Gateway 内交付一个可真实运行的后端垂直切片：Task 产品
模型、独立 SQLite、FIFO/并发、Pi 0.84.2 RPC 进程、暂停/session 恢复/取消/删除、
`ask_main_agent` 等待、REST 与 SSE，以及完整的故障、重启与 shutdown 收口。

~~~mermaid
flowchart LR
  GW["FastAPI REST/SSE"] --> TM["TaskManager"]
  TM --> DB["Task SQLite"]
  TM --> Q["FIFO Scheduler"]
  TM --> P["PiProcessSupervisor"]
  P --> PI["Pinned Pi 0.84.2 RPC"]
~~~

## 实施批次

1. 更新设计事实源与职责文档。
2. TDD 建立 `pi-aurabot-task` 和私有 `pi-runtime`。
3. TDD 实现模型、配置和独立 Python SQLite Store。
4. TDD 实现严格 JSONL RPC 与唯一 subprocess owner。
5. TDD 实现 FIFO Scheduler 与 TaskManager 生命周期。
6. 接入 camelCase REST、SSE replay 与 FastAPI lifespan。
7. 运行真实 Pi offline smoke、Backend/Frontend 回归和残留审计，并切换文档状态。

每个批次都先运行预期失败的边界测试，再写最小实现，随后执行局部回归。最终检查
Package typecheck/test/pack、Pi 版本、Backend compile/unittest/Uvicorn、Frontend
typecheck/test/build、OpenAPI、残留符号、源码文件头注释、`git diff --check` 和子进程
清理。

## 非目标

Electron Task UI/IPC、Main Agent 真实接入、Artifact、Task Profile、Package UI、生产
发行打包、远程 Worker、Node SDK Worker、true resume、自研权限/sandbox/MCP、
多机调度和多 Agent 框架均延期。

## 最终交付表述

**Python Pi Task 后端垂直切片已实现；Electron、Main Agent、Artifact 与发行打包尚未接入。**
