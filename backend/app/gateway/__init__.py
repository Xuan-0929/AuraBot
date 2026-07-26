"""
组织 Backend 对外 HTTP routes、Pydantic models 与依赖/错误映射。

FastAPI app 注册这里的 router，route 只调用 application service 并转换传输值；
本包不直接访问 runtime record、不实现 Task/Plan 业务，也不管理进程生命周期。
"""
