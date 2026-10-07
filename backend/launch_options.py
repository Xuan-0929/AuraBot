"""Read the process-only options explicitly passed by Electron to every Backend child.

These immutable flags gate diagnostics and select a session-owned Profile storage root. They never
modify settings; locked startup fails when its temporary directory is absent instead of using real data.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MEMORY_FIXTURE = 'benchmark-memory-v1'


@dataclass(frozen=True)
class LaunchOptions:
    debug: bool = False
    lock_memory: bool = False
    session_data_dir: Path | None = None

    def __post_init__(self):
        if self.lock_memory and (self.session_data_dir is None or not self.session_data_dir.is_absolute()
                or not self.session_data_dir.is_dir() or self.session_data_dir.is_symlink()):
            raise ValueError('locked_memory_requires_session_directory')

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> LaunchOptions:
        env = os.environ if env is None else env
        for name in ('AURABOT_DEBUG', 'AURABOT_LOCK_MEMORY'):
            if env.get(name, '0') not in ('0', '1'):
                raise ValueError(f'invalid_launch_option:{name}')
        path = env.get('AURABOT_SESSION_DATA_DIR')
        return cls(env.get('AURABOT_DEBUG') == '1', env.get('AURABOT_LOCK_MEMORY') == '1', Path(path) if path else None)

    def public(self) -> dict[str, bool]:
        return {'debug': self.debug, 'lockMemory': self.lock_memory}
