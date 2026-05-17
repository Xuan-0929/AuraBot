# 任务计划：记忆系统文档迁移

## 目标
把 AuraBot 中关于 Supermemory 接入的文档，迁移为“本地节点 + 边图结构记忆”的开源方案调研与落地路线；同时生成一份 HTML，说明当前记忆系统和改进方向。

## 当前阶段
已完成

## 阶段

### 阶段 1：外部调研与现状确认
- [x] 调研市面上的开源图结构 / 长期记忆项目
- [x] 阅读 AuraBot 当前记忆结构文档和核心实现
- [x] 记录关键发现到 `findings.md`
- **状态：** 已完成

### 阶段 2：仓库文档改写
- [x] 移除 Supermemory 接入导向，改为开源图记忆路线
- [x] 更新当前记忆结构文档中的日期、边界和下一步
- [x] 检查 README 文档索引是否需要同步
- **状态：** 已完成

### 阶段 3：HTML 汇报页
- [x] 创建说明当前系统和改进方向的 HTML
- [x] 确保页面可直接打开、中文可读、结构清晰
- **状态：** 已完成

### 阶段 4：校验与交付
- [x] 全仓搜索确认不再保留 Supermemory 接入方案口径
- [x] 运行格式/空白检查
- [x] 汇总修改文件和调研来源
- **状态：** 已完成

## 已做决策
| 决策 | 理由 |
| --- | --- |
| 不再建议 AuraBot 接入 Supermemory | 用户已明确 Supermemory 闭源且团队不准备接入。 |
| 保留 AuraBot 本地 SQLite 图结构作为主线 | 当前代码已有 `memory_nodes`、`memory_edges`、bounded graph traversal、debug trace 和 follow-up scheduler，迁移成本最低。 |
| 重点借鉴 Graphiti、Cognee、Mem0、Letta | 这些项目分别代表时间知识图谱、图/向量管线、生产化记忆层、分层/自编辑记忆，能形成互补参考。 |

## 遇到的问题
| 问题 | 尝试 | 处理结果 |
| --- | --- | --- |
| Playwright 渲染检查缺少浏览器二进制 | 使用项目 `@playwright/test` 启动 Chromium | 本机未安装 Playwright browser；未下载新依赖，改用 `jsdom` 做 HTML DOM 结构校验。 |

## 备注
- 文档以中文为主。
- 外部调研需要在最终交付中注明来源链接。
