# SQLite表格存储内容

> 对于SQLite的建表脚本必须是幂等的。


## Skill元信息


- INTEGER/主键/自增：Skill ID
- INTEGER/外键/非空：属于的SkillGroup ID
- TEXT/非空：Skill名称
- TEXT/非空：Skill在本机的URL或者基于项目根目录的URL。
- BOOL/非空：Skill是项目原生的还是后续用户安装的
- BOOL/非空：Skill是基于全局URL的还是基于项目根目录的URL。

约束：

- 同一个SkillGroup内Skill名称唯一：`UNIQUE (group_id, name)`
- 不同SkillGroup之间允许Skill名称重复




## SkillGroup元信息


- INTEGER/主键/自增：SkillGroup ID
- TEXT/唯一/非空：SkillGroup名称
- TEXT：描述信息
- BOOL/非空：SkillGroup是项目原生的还是后续用户安装的


## 建表脚本

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS skill_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS skill (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    is_global_url INTEGER NOT NULL DEFAULT 0 CHECK (is_global_url IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (group_id)
        REFERENCES skill_group(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    UNIQUE (group_id, name)
);

CREATE INDEX IF NOT EXISTS idx_skill_group_id
ON skill(group_id);

CREATE INDEX IF NOT EXISTS idx_skill_name
ON skill(name);
```
