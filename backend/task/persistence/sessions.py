"""协调 Task session 隔离删除与 SQLite tombstone 的可恢复一致性。

TaskSessionDeletion 独占 session root 下的 canonical 与 ``.deleting`` 路径：删除时先原子
移动 session，再调用 TaskStore 写 tombstone，最后清理隔离目录；启动时依据数据库事实
恢复或清理残留。它不拥有 SQLite connection，数据库事务仍由 TaskStore 管理。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from uuid import UUID

from ..models import (
    TERMINAL_TASK_STATUSES,
    TaskEvent,
    TaskNotFound,
    TaskRecord,
    TaskStateConflict,
    TaskStatus,
)
from .store import TaskStore

logger = logging.getLogger(__name__)


class TaskSessionCleanupError(RuntimeError):
    """Raised when session quarantine cannot converge safely with its tombstone."""

    def __init__(self, *, tombstone_committed: bool) -> None:
        self.tombstone_committed = tombstone_committed
        super().__init__("Task session cleanup failed.")


class TaskSessionDeletion:
    """Delete and recover only UUID-derived Task session paths under one root."""

    def __init__(self, store: TaskStore, session_root: Path) -> None:
        self._store = store
        self.session_root = session_root.resolve()

    async def initialize(self) -> None:
        """Create the session root and reject an unsafe quarantine root."""

        self.session_root.mkdir(parents=True, exist_ok=True)
        self._require_safe_deleting_root()

    async def delete(self, task_id: UUID) -> tuple[TaskRecord, TaskEvent]:
        """Quarantine a terminal session, write one tombstone, then strongly purge it."""

        self._require_safe_deleting_root()
        task = await self._store.get_task(task_id, include_deleted=True)
        if task.deleted_at is not None:
            canonical, staged = self._session_paths(task_id)
            if self._lexists(canonical) and self._lexists(staged):
                raise TaskSessionCleanupError(tombstone_committed=True)
            if self._lexists(canonical):
                staged.parent.mkdir(parents=True, exist_ok=True)
                os.replace(canonical, staged)
            await self._purge_staged_session(task_id)
            return await self._store.tombstone_task(task_id)
        if task.status not in TERMINAL_TASK_STATUSES:
            raise TaskStateConflict(task.status, TaskStatus.CANCELLED)

        moved = self._stage_session(task_id)
        try:
            deleted, event = await self._store.tombstone_task(task_id)
        except BaseException as error:
            try:
                if moved:
                    self._restore_staged_session(task_id)
            except BaseException:
                logger.critical(
                    "Failed to restore Task session after tombstone rollback",
                    exc_info=True,
                )
                raise TaskSessionCleanupError(tombstone_committed=False) from error
            raise
        try:
            await self._purge_staged_session(task_id)
        except OSError as error:
            raise TaskSessionCleanupError(tombstone_committed=True) from error
        return deleted, event

    async def reconcile(self) -> None:
        """Restore live sessions and purge deleted sessions left in quarantine."""

        deleting = self.session_root / ".deleting"
        self._require_safe_deleting_root()
        if not deleting.is_dir():
            return
        for staged in deleting.iterdir():
            try:
                task_id = UUID(staged.name)
            except ValueError:
                logger.warning("Ignoring non-UUID Task deletion entry: %s", staged)
                continue
            if str(task_id) != staged.name:
                logger.warning("Ignoring non-canonical Task deletion entry: %s", staged)
                continue
            try:
                task = await self._store.get_task(task_id, include_deleted=True)
            except TaskNotFound:
                logger.warning("Ignoring unknown Task deletion entry: %s", staged)
                continue
            canonical, _ = self._session_paths(task_id)
            if self._lexists(canonical):
                raise TaskSessionCleanupError(
                    tombstone_committed=task.deleted_at is not None
                )
            if task.deleted_at is None:
                self._restore_staged_session(task_id)
            else:
                await self._purge_staged_session(task_id)

    def _session_paths(self, task_id: UUID) -> tuple[Path, Path]:
        canonical_id = UUID(str(task_id))
        name = str(canonical_id)
        return self.session_root / name, self.session_root / ".deleting" / name

    @staticmethod
    def _lexists(path: Path) -> bool:
        return os.path.lexists(path)

    def _require_safe_deleting_root(self) -> None:
        deleting = self.session_root / ".deleting"
        if deleting.is_symlink() or (
            self._lexists(deleting) and not deleting.is_dir()
        ):
            logger.critical("Task deletion quarantine root is unsafe: %s", deleting)
            raise TaskSessionCleanupError(tombstone_committed=False)

    def _stage_session(self, task_id: UUID) -> bool:
        canonical, staged = self._session_paths(task_id)
        if self._lexists(canonical) and self._lexists(staged):
            raise TaskSessionCleanupError(tombstone_committed=False)
        if not self._lexists(canonical):
            return False
        staged.parent.mkdir(parents=True, exist_ok=True)
        os.replace(canonical, staged)
        return True

    def _restore_staged_session(self, task_id: UUID) -> None:
        canonical, staged = self._session_paths(task_id)
        if not self._lexists(staged):
            return
        if self._lexists(canonical):
            raise TaskSessionCleanupError(tombstone_committed=False)
        os.replace(staged, canonical)

    async def _purge_staged_session(self, task_id: UUID) -> None:
        _, staged = self._session_paths(task_id)
        if not self._lexists(staged):
            return
        if staged.is_symlink() or not staged.is_dir():
            staged.unlink()
        else:
            shutil.rmtree(staged)


__all__ = ["TaskSessionCleanupError", "TaskSessionDeletion"]
