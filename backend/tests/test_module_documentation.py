"""
验证 Backend agent/app 与指定 MCP 入口持续具备完整中文模块说明。

测试以 AST 读取真实生产文件，检查职责说明至少包含两行且含中文；它不校验文案措辞，
也不要求函数级注释，从而只守住 AGENTS.md 规定的文件边界文档契约。
"""

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    PROJECT_ROOT / "backend" / "agent",
    PROJECT_ROOT / "backend" / "app",
)
STANDALONE_MODULES = (
    PROJECT_ROOT / "backend" / "main.py",
    PROJECT_ROOT / "mcp_server" / "Plan" / "tools.py",
    PROJECT_ROOT / "mcp_server" / "scripts" / "http_smoke_test.py",
)


def production_modules() -> tuple[Path, ...]:
    """Return the maintained Python source boundary in deterministic order."""

    package_modules = (
        module
        for root in SOURCE_ROOTS
        for module in root.rglob("*.py")
    )
    return tuple(sorted((*package_modules, *STANDALONE_MODULES)))


class ModuleDocumentationTests(unittest.TestCase):
    def test_production_modules_explain_their_boundary_in_chinese(self):
        failures: list[str] = []
        for module in production_modules():
            tree = ast.parse(module.read_text(encoding="utf-8"))
            docstring = ast.get_docstring(tree) or ""
            non_empty_lines = tuple(
                line.strip()
                for line in docstring.splitlines()
                if line.strip()
            )
            has_chinese = any(
                "\u4e00" <= character <= "\u9fff"
                for character in docstring
            )
            if len(non_empty_lines) < 2 or not has_chinese:
                failures.append(str(module.relative_to(PROJECT_ROOT)))

        self.assertEqual(
            failures,
            [],
            "以下生产模块缺少完整中文文件说明：\n"
            + "\n".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
