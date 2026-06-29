from pathlib import Path


class Skill:
    def __init__(self, name: str, path: str | Path | None = None):
        self.name = name
        self.path = Path(path or name)

    def load(self) -> str:
        content = self.path.read_text(encoding="utf-8")

        if not content.startswith("---"):
            return content

        lines = content.splitlines(keepends=True)

        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return "".join(lines[index + 1 :]).lstrip("\r\n")

        return content
