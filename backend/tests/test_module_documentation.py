"""
验证保留的 Backend Python 源文件持续具备完整中文模块说明。

测试以 AST 扫描 backend 下除 tests 外的生产模块，检查职责说明至少包含两行且含中文；
它不校验具体措辞，只守住 AGENTS.md 规定的文件顶部边界文档契约。
"""

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"


def production_modules() -> tuple[Path, ...]:
    """Return the maintained Python source boundary in deterministic order."""

    app_modules = tuple(sorted((BACKEND_ROOT / "app").rglob("*.py")))
    task_modules = tuple(sorted((BACKEND_ROOT / "task").rglob("*.py")))
    return (
        *app_modules,
        *task_modules,
        BACKEND_ROOT / "config.py",
        BACKEND_ROOT / "main.py",
    )


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
