"""暴露 Main Agent 的配置、Profile、Canonical Context 与快照基础边界。

TaskManager 继续只通过 settings 的受信任 WorkspaceRegistry 获取 cwd；实时 runtime 则从此包
取得 ProfileStore、ContextStore 和 SnapshotProjector。后续 Tool/Scheduler 模块依赖这些已验证
事实源，不自行读写人格文件或伪造已送达的助手语言。
"""

from .context import ClauseState, ContextStore, FoldedContext, SpeechClause
from .profiles import Profile, ProfileConflictError, ProfileStore, ProfileValidationError, SoulDocument
from .settings import MainAgentSettings, WorkspaceRegistry, WorkspaceRegistryError
from .snapshot import Snapshot, SnapshotBudget, SnapshotProjector

__all__ = [
    "ClauseState", "ContextStore", "FoldedContext", "MainAgentSettings", "Profile",
    "ProfileConflictError", "ProfileStore", "ProfileValidationError", "Snapshot", "SnapshotBudget",
    "SnapshotProjector", "SoulDocument", "SpeechClause", "WorkspaceRegistry", "WorkspaceRegistryError",
]
