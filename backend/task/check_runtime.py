"""Check a source installation offline using the same TaskSettings validation as Backend startup.

The Node installer supplies its data root and executable; the prepared backend environment resolves
Bash and validates Pi, extension and prompt files without reading settings, contacting a model,
or opening Task storage. Print selected paths for troubleshooting and exit nonzero on failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .settings import EXPECTED_PI_VERSION, TaskSettings, TaskSettingsError


def main() -> int:
    # Windows redirected output can otherwise fail on a Chinese installation path.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--node", required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    settings = TaskSettings.from_mapping(
        {"task": {"user_data_dir": str(root), "pi_runtime_dir": str(root / "pi-runtime"), "node_executable": args.node}},
        config_root=root,
    )
    for label, value in (
        ("AuraBot data directory", root), ("Python", sys.executable),
        ("Node", settings.node_executable), ("Pi CLI", settings.pi_cli_path),
        ("Extension", settings.plugin_entry_path), ("System prompt", settings.system_prompt_path),
    ):
        print(f"{label}: {value}", flush=True)
    if sys.platform == "win32":
        print(f"Git Bash: {settings.shell_path or 'MISSING; install Git for Windows to enable tasks.'}", flush=True)
    try:
        settings.validate_runtime()
    except TaskSettingsError as error:
        print(f"Runtime check failed [{error.code}]: {error}", file=sys.stderr)
        return 1
    print(f"Pi version: {EXPECTED_PI_VERSION}; local runtime check passed (no model request).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
