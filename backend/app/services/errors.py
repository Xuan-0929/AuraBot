"""
定义 application service 向 transport 暴露的稳定错误载体。

各 service 在捕获领域或 runtime 异常时转换为 ApplicationError，gateway 再映射状态码；
本模块不选择 HTTP 响应，也不保存底层异常处理逻辑。
"""

from __future__ import annotations


class ApplicationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


__all__ = ["ApplicationError"]
