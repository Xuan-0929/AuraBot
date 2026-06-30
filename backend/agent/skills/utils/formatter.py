





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

        metadata[key] = Skill._unquote_yaml_scalar(value)

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