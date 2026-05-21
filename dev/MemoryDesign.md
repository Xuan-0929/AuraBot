# 记忆设计


## 记忆系统模块抽象

记忆系统运行时对外暴露两个接口：
- `remember(content: list[dict[str, str]]): bool`
- `recall(query: str): dict[str, float]`

记忆系统开发时可能还会包括list，add，delete接口用于debug。


## 