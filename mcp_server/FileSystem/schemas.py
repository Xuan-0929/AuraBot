"""MCP Tool definitions specified by 项目原生工具.md."""

from __future__ import annotations

from typing import Any

from mcp.types import Tool


LIST_DIRECTORY_DESCRIPTION = (
    "列出一个目录的直接子项，不递归。返回文件、目录、符号链接和其他类型；包含隐藏项，"
    "但过滤名称为 .git 的直接子项。相对路径以当前 Task 工作目录为基准，访问范围受 "
    "Task 文件系统权限限制。结果最多返回 2000 项；结果被截断时应改用 glob 缩小范围。"
)
READ_FILE_DESCRIPTION = (
    "按行读取 UTF-8 文本文件，返回不带人工行号的原始文本片段。offset 是从 0 开始的"
    "起始行索引；limit 是最多返回的行数，默认 2000，最大 2000。返回完整文件的 "
    "SHA-256，供 edit_file 做并发修改保护。二进制文件或无效 UTF-8 返回错误。"
)
STAT_DESCRIPTION = (
    "读取一个文件系统对象的元数据，不读取文件内容。对象不存在、路径越权或无法访问时"
    "返回错误。符号链接返回链接自身信息，不把链接目标伪装成普通文件或目录。"
)
GLOB_DESCRIPTION = (
    "在 root 下按 glob pattern 查找文件系统对象。pattern 相对 root 匹配，支持 *、?、"
    "字符组和 **；路径分隔符统一写作 /。包含隐藏项，不遍历目录符号链接。结果最多返回 "
    "2000 项。"
)
SEARCH_FILES_DESCRIPTION = (
    "在 root 下递归搜索 UTF-8 文本文件内容。query 是正则表达式；glob 可选，用于限制"
    "相对 root 的文件路径，例如 **/*.py。忽略二进制文件，不遍历目录符号链接。每个"
    "结果代表一个匹配位置，行号和列号从 1 开始；最多返回 1000 个结果。"
)
CREATE_FILE_DESCRIPTION = (
    "创建一个空的普通文件。url 可以是本地 file:// URL、Task 工作目录相对路径或已授权"
    "的绝对路径，不接受 http/https 等远程 URL。目标已存在或父目录不存在时返回错误；"
    "本 Tool 不覆盖文件，也不自动创建父目录。"
)
EDIT_FILE_DESCRIPTION = (
    "原子编辑一个已存在的 UTF-8 文本文件。按 edits 顺序执行精确文本替换；默认要求 "
    "old_text 在当时内容中恰好出现一次，replace_all=true 时替换全部出现位置。"
    "expected_sha256 可校验 read_file 返回的文件版本，避免覆盖并发修改。任一校验或"
    "替换失败时不写入任何内容。"
)
BASH_DESCRIPTION = (
    "在 PATH 指定的 Task 沙箱目录中通过非交互式 Bash 执行一段 statement，返回 stdout、"
    "stderr 和退出状态。PATH 可以是 Task 工作目录相对路径或沙箱内绝对路径，由 "
    "FileSystemAdaptor 规范化为绝对路径。每次调用使用独立 shell，不保留 cd、变量、"
    "alias 或 shell option。命令受固定超时、输出上限和基础恶意命令拦截约束；超时或"
    "输出截断会在结果中明确标记。"
)


def _object_schema(
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PATH_INPUT = {
    "type": "string",
    "minLength": 1,
    "description": "要操作的文件系统路径。",
}
KIND = {
    "type": "string",
    "enum": ["file", "directory", "symlink", "other"],
    "description": "文件系统对象类型。",
}
NULLABLE_SIZE = {
    "type": ["integer", "null"],
    "minimum": 0,
    "description": "普通文件大小；其他类型为 null。",
}
SHA256 = {
    "type": "string",
    "pattern": "^[0-9a-f]{64}$",
    "description": "文件内容的小写 SHA-256。",
}


LIST_DIRECTORY_INPUT = _object_schema(
    {
        "path": {
            **PATH_INPUT,
            "description": "要列出的目录路径；可以是 Task 工作目录相对路径或已授权的绝对路径。",
        }
    },
    ["path"],
)
LIST_DIRECTORY_OUTPUT = _object_schema(
    {
        "path": {"type": "string", "description": "目录的规范化绝对路径。"},
        "entries": {
            "type": "array",
            "maxItems": 2000,
            "items": _object_schema(
                {
                    "name": {"type": "string", "description": "目录项名称。"},
                    "path": {"type": "string", "description": "目录项的规范化绝对路径。"},
                    "kind": {
                        **KIND,
                        "description": "目录项类型。",
                    },
                    "size_bytes": {
                        **NULLABLE_SIZE,
                        "description": "普通文件大小；其他类型为 null。",
                    },
                },
                ["name", "path", "kind", "size_bytes"],
            ),
        },
        "total_entries": {
            "type": "integer",
            "minimum": 0,
            "description": "过滤 .git 后的目录直接子项总数。",
        },
        "truncated": {
            "type": "boolean",
            "description": "是否因 2000 项上限截断 entries。",
        },
    },
    ["path", "entries", "total_entries", "truncated"],
)

READ_FILE_INPUT = _object_schema(
    {
        "path": {**PATH_INPUT, "description": "要读取的文件路径。"},
        "offset": {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "description": "从 0 开始的起始行索引；省略时从文件首行读取。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2000,
            "default": 2000,
            "description": "最多返回的行数；省略时为 2000。",
        },
    },
    ["path"],
)
READ_FILE_OUTPUT = _object_schema(
    {
        "path": {"type": "string", "description": "文件的规范化绝对路径。"},
        "content": {
            "type": "string",
            "description": "选定行范围的原始 UTF-8 文本，不添加行号；保留范围内原有换行符。",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": "本次读取使用的起始行索引。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2000,
            "description": "本次读取使用的行数上限。",
        },
        "returned_lines": {
            "type": "integer",
            "minimum": 0,
            "description": "content 实际包含的行数。",
        },
        "total_lines": {
            "type": "integer",
            "minimum": 0,
            "description": "完整文件总行数。",
        },
        "size_bytes": {
            "type": "integer",
            "minimum": 0,
            "description": "完整文件大小。",
        },
        "sha256": {
            **SHA256,
            "description": "完整文件内容的小写 SHA-256。",
        },
        "truncated": {
            "type": "boolean",
            "description": "offset + returned_lines 之后是否仍有未返回内容。",
        },
    },
    [
        "path",
        "content",
        "offset",
        "limit",
        "returned_lines",
        "total_lines",
        "size_bytes",
        "sha256",
        "truncated",
    ],
)

STAT_INPUT = _object_schema(
    {
        "path": {
            **PATH_INPUT,
            "description": "要检查的文件、目录、符号链接或其他文件系统对象路径。",
        }
    },
    ["path"],
)
STAT_OUTPUT = _object_schema(
    {
        "path": {"type": "string", "description": "对象的规范化绝对路径。"},
        "name": {"type": "string", "description": "对象名称。"},
        "kind": {**KIND, "description": "对象类型。"},
        "size_bytes": {
            **NULLABLE_SIZE,
            "description": "普通文件大小；其他类型为 null。",
        },
        "modified_at": {
            "type": "string",
            "format": "date-time",
            "description": "最后修改时间，使用 RFC 3339。",
        },
        "readonly": {
            "type": "boolean",
            "description": "当前进程是否缺少该对象的写权限。",
        },
        "symlink_target": {
            "type": ["string", "null"],
            "description": "符号链接记录的目标文本；非符号链接为 null。",
        },
    },
    [
        "path",
        "name",
        "kind",
        "size_bytes",
        "modified_at",
        "readonly",
        "symlink_target",
    ],
)

GLOB_INPUT = _object_schema(
    {
        "pattern": {
            "type": "string",
            "minLength": 1,
            "description": "相对 root 的 glob pattern，例如 **/*.py 或 src/**/test_*.ts。",
        },
        "root": {**PATH_INPUT, "description": "搜索根目录。"},
    },
    ["pattern", "root"],
)
GLOB_OUTPUT = _object_schema(
    {
        "root": {"type": "string", "description": "搜索根目录的规范化绝对路径。"},
        "pattern": {"type": "string", "description": "本次使用的 glob pattern。"},
        "matches": {
            "type": "array",
            "maxItems": 2000,
            "items": _object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "匹配对象的规范化绝对路径。",
                    },
                    "kind": {**KIND, "description": "匹配对象类型。"},
                },
                ["path", "kind"],
            ),
        },
        "total_matches": {
            "type": "integer",
            "minimum": 0,
            "description": "完整匹配数量。",
        },
        "truncated": {
            "type": "boolean",
            "description": "是否因 2000 项上限截断 matches。",
        },
    },
    ["root", "pattern", "matches", "total_matches", "truncated"],
)

SEARCH_FILES_INPUT = _object_schema(
    {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "要搜索的正则表达式；无效表达式返回错误。",
        },
        "root": {**PATH_INPUT, "description": "内容搜索根目录。"},
        "glob": {
            "type": "string",
            "minLength": 1,
            "description": "可选的文件路径 glob 过滤器，相对 root 匹配。",
        },
    },
    ["query", "root"],
)
SEARCH_FILES_OUTPUT = _object_schema(
    {
        "root": {"type": "string", "description": "搜索根目录的规范化绝对路径。"},
        "query": {"type": "string", "description": "本次使用的正则表达式。"},
        "glob": {
            "type": ["string", "null"],
            "description": "本次使用的文件 glob；未提供时为 null。",
        },
        "matches": {
            "type": "array",
            "maxItems": 1000,
            "items": _object_schema(
                {
                    "path": {
                        "type": "string",
                        "description": "匹配文件的规范化绝对路径。",
                    },
                    "line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "匹配起始行号。",
                    },
                    "column": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "匹配起始列号，按 Unicode 字符计数。",
                    },
                    "match": {
                        "type": "string",
                        "description": "query 实际匹配的文本。",
                    },
                    "text": {
                        "type": "string",
                        "description": "匹配所在整行，不包含行尾换行符。",
                    },
                },
                ["path", "line", "column", "match", "text"],
            ),
        },
        "total_matches": {
            "type": "integer",
            "minimum": 0,
            "description": "完整匹配数量。",
        },
        "truncated": {
            "type": "boolean",
            "description": "是否因 1000 个结果上限截断 matches。",
        },
    },
    ["root", "query", "glob", "matches", "total_matches", "truncated"],
)

CREATE_FILE_INPUT = _object_schema(
    {
        "url": {
            "type": "string",
            "minLength": 1,
            "description": "要创建的本地文件 URL 或文件路径。",
        }
    },
    ["url"],
)
CREATE_FILE_OUTPUT = _object_schema(
    {
        "path": {"type": "string", "description": "新文件的规范化绝对路径。"},
        "created": {
            "type": "boolean",
            "const": True,
            "description": "成功时固定为 true。",
        },
        "size_bytes": {
            "type": "integer",
            "const": 0,
            "description": "新建空文件的大小，固定为 0。",
        },
        "sha256": {
            **SHA256,
            "description": "新建空文件内容的小写 SHA-256。",
        },
    },
    ["path", "created", "size_bytes", "sha256"],
)

EDIT_FILE_INPUT = _object_schema(
    {
        "path": {
            **PATH_INPUT,
            "description": "要编辑的已存在 UTF-8 文本文件路径。",
        },
        "edits": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": _object_schema(
                {
                    "old_text": {
                        "type": "string",
                        "description": "必须在当前中间内容中匹配的原始文本。",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "用于替换 old_text 的新文本；空字符串表示删除。",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否替换 old_text 的全部出现位置；省略时为 false。",
                    },
                },
                ["old_text", "new_text"],
            ),
        },
        "expected_sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
            "description": "可选的修改前文件 SHA-256，通常取自最近一次 read_file。",
        },
    },
    ["path", "edits"],
)
EDIT_FILE_OUTPUT = _object_schema(
    {
        "path": {"type": "string", "description": "文件的规范化绝对路径。"},
        "changed": {"type": "boolean", "description": "最终文件内容是否发生变化。"},
        "edit_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "成功执行的 edit 项数量。",
        },
        "replacement_count": {
            "type": "integer",
            "minimum": 1,
            "description": "所有 edit 实际替换位置的总数。",
        },
        "previous_sha256": {
            **SHA256,
            "description": "修改前文件内容的小写 SHA-256。",
        },
        "sha256": {
            **SHA256,
            "description": "修改后文件内容的小写 SHA-256。",
        },
        "size_bytes": {
            "type": "integer",
            "minimum": 0,
            "description": "修改后文件大小。",
        },
    },
    [
        "path",
        "changed",
        "edit_count",
        "replacement_count",
        "previous_sha256",
        "sha256",
        "size_bytes",
    ],
)

BASH_INPUT = _object_schema(
    {
        "statement": {
            "type": "string",
            "minLength": 1,
            "description": "交给 Bash 执行的完整 statement。",
        },
        "PATH": {
            **PATH_INPUT,
            "description": "Bash 工作目录；可以是 Task 工作目录相对路径或沙箱内绝对路径。",
        },
    },
    ["statement", "PATH"],
)
BASH_OUTPUT = _object_schema(
    {
        "cwd": {"type": "string", "description": "本次命令使用的规范化绝对工作目录。"},
        "stdout": {
            "type": "string",
            "description": "命令标准输出；达到输出上限时只包含服务端保留部分。",
        },
        "stderr": {
            "type": "string",
            "description": "命令标准错误；达到输出上限时只包含服务端保留部分。",
        },
        "exit_code": {
            "type": ["integer", "null"],
            "description": "进程退出码；进程未能正常取得退出码时为 null。",
        },
        "timed_out": {
            "type": "boolean",
            "description": "命令是否因固定超时被终止。",
        },
        "truncated": {
            "type": "boolean",
            "description": "stdout 或 stderr 是否因输出上限被截断。",
        },
        "duration_ms": {
            "type": "integer",
            "minimum": 0,
            "description": "本次执行耗时，单位毫秒。",
        },
    },
    [
        "cwd",
        "stdout",
        "stderr",
        "exit_code",
        "timed_out",
        "truncated",
        "duration_ms",
    ],
)


TOOL_DEFINITIONS = (
    Tool(
        name="list_directory",
        description=LIST_DIRECTORY_DESCRIPTION,
        inputSchema=LIST_DIRECTORY_INPUT,
        outputSchema=LIST_DIRECTORY_OUTPUT,
    ),
    Tool(
        name="read_file",
        description=READ_FILE_DESCRIPTION,
        inputSchema=READ_FILE_INPUT,
        outputSchema=READ_FILE_OUTPUT,
    ),
    Tool(
        name="stat",
        description=STAT_DESCRIPTION,
        inputSchema=STAT_INPUT,
        outputSchema=STAT_OUTPUT,
    ),
    Tool(
        name="glob",
        description=GLOB_DESCRIPTION,
        inputSchema=GLOB_INPUT,
        outputSchema=GLOB_OUTPUT,
    ),
    Tool(
        name="search_files",
        description=SEARCH_FILES_DESCRIPTION,
        inputSchema=SEARCH_FILES_INPUT,
        outputSchema=SEARCH_FILES_OUTPUT,
    ),
    Tool(
        name="create_file",
        description=CREATE_FILE_DESCRIPTION,
        inputSchema=CREATE_FILE_INPUT,
        outputSchema=CREATE_FILE_OUTPUT,
    ),
    Tool(
        name="edit_file",
        description=EDIT_FILE_DESCRIPTION,
        inputSchema=EDIT_FILE_INPUT,
        outputSchema=EDIT_FILE_OUTPUT,
    ),
    Tool(
        name="bash",
        description=BASH_DESCRIPTION,
        inputSchema=BASH_INPUT,
        outputSchema=BASH_OUTPUT,
    ),
)

TOOL_BY_NAME = {tool.name: tool for tool in TOOL_DEFINITIONS}

__all__ = ["TOOL_BY_NAME", "TOOL_DEFINITIONS"]
