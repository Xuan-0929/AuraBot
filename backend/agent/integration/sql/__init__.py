"""
组织 agent 领域对象的 SQL adapters 与共享 transaction 工具。

各 manager 或配置层通过具体 adapter 读写 Skill、Tool、Group、MCP server 和 adaptor
声明；本包不维护运行时缓存、不执行领域能力，也不拥有数据库连接生命周期。
"""
