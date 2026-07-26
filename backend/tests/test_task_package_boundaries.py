"""验证 Task 重组后的目录和公开门面边界；读取真实包树并以 AST 检查直接相对导入，
为 domain/runtime/events/harness 迁移提供驱动，不负责移动模块或验证运行时行为。"""

import ast
import unittest
from pathlib import Path


EXPECTED_TASK_ROOT = {"__init__.py", "domain", "runtime", "events", "harness"}
EXPECTED_HARNESS_ROOT = {
    "__init__.py",
    "core",
    "strategies",
    "adaptors",
    "prompting",
}

TASK_ROOT = Path(__file__).resolve().parents[1] / "agent" / "task"
HARNESS_ROOT = TASK_ROOT / "harness"


def package_entries(path: Path) -> set[str]:
    """Return source-package children while ignoring interpreter caches."""

    return {
        child.name
        for child in path.iterdir()
        if child.suffix == ".py"
        or (child.is_dir() and not child.name.startswith("__"))
    }


def relative_import_roots(init_file: Path) -> set[str]:
    """Return the first segment of direct relative imports in a package facade."""

    tree = ast.parse(init_file.read_text(encoding="utf-8"))
    return {
        statement.module.split(".", maxsplit=1)[0]
        for statement in ast.walk(tree)
        if isinstance(statement, ast.ImportFrom)
        and statement.level == 1
        and statement.module is not None
    }


class TaskPackageBoundaryTests(unittest.TestCase):
    def test_task_root_contains_only_the_final_subpackages(self):
        self.assertEqual(package_entries(TASK_ROOT), EXPECTED_TASK_ROOT)

    def test_harness_root_contains_only_the_final_subpackages(self):
        self.assertEqual(package_entries(HARNESS_ROOT), EXPECTED_HARNESS_ROOT)

    def test_task_public_facade_imports_only_domain_and_runtime(self):
        self.assertEqual(
            relative_import_roots(TASK_ROOT / "__init__.py"),
            {"domain", "runtime"},
        )

    def test_harness_public_facade_imports_only_core_and_strategies(self):
        harness_init = HARNESS_ROOT / "__init__.py"
        self.assertTrue(harness_init.is_file())
        self.assertEqual(
            relative_import_roots(harness_init),
            {"core", "strategies"},
        )


if __name__ == "__main__":
    unittest.main()
