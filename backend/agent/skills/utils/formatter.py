





"""
负责解析 Skill 文件 frontmatter 并生成领域构造所需的稳定字段。

Skill 文件加载流程把文本交给这里提取 metadata 与正文，再由 Skill 模型完成约束；
本模块不访问 manager、不执行技能，也不参与 Harness prompt 编排。
"""


def parse_frontmatter(metadata_text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in metadata_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        metadata[key] = unquote_yaml_scalar(value)

    return metadata





def unquote_yaml_scalar(value: str) -> str:
    if len(value) < 2:
        return value

    quote = value[0]
    if quote not in ("'", '"') or value[-1] != quote:
        return value

    unquoted = value[1:-1]
    if quote == "'":
        return unquoted.replace("''", "'")

    return (
        unquoted.replace(r"\\", "\\")
        .replace(r"\"", '"')
        .replace(r"\n", "\n")
        .replace(r"\t", "\t")
    )
