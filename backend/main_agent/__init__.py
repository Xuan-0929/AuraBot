"""暴露 Main Agent 的配置、Context、快照、调度与渲染交付边界。

TaskManager 继续只通过 settings 的受信任 WorkspaceRegistry 获取 cwd；实时 runtime 则从此包
取得 ProfileStore、ContextStore、SnapshotProjector、Observation 安全读取器、TaskBridge 和严格
单飞 MainAgentRuntime，以及只指向一个 Renderer generation 的不可变 Realtime lease port。
Provider、Renderer 与 Tool 适配器注入这些边界，不自行读写人格文件、伪造已送达的助手语言或
绕过 TaskManager。
"""

from .context import ClauseState, ContextStore, FoldedContext, SpeechClause
from .observations import (
    ObservationExecutorError,
    ObservationLedger,
    ObservationSecurityError,
    SafeWebReader,
    SourceRegistry,
    TavilySearchExecutor,
    WorkspaceListExecutor,
    WorkspaceReadExecutor,
    WorkspaceReader,
)
from .profiles import Profile, ProfileConflictError, ProfileStore, ProfileValidationError, SoulDocument
from .realtime import (
    AUR1_HEADER_SIZE,
    AudioFrame,
    AudioFrameKind,
    AudioFrameTracker,
    BoundedAudioQueue,
    CapabilityTokens,
    OutboundWriter,
    ProtocolError,
    RealtimeHub,
    RendererLeasePort,
    SUBPROTOCOL,
    pack_audio_frame,
    parse_audio_frame,
    validate_control_message,
)
from .runtime import (
    ActiveInput,
    InvokeRequest,
    MainAgentRuntime,
    PendingTaskProposal,
    PendingTrigger,
    PrepareAdmissionPolicy,
    RendererClause,
    TaskProposalStatus,
)
from .settings import MainAgentSettings, WorkspaceRegistry, WorkspaceRegistryError
from .snapshot import Snapshot, SnapshotBudget, SnapshotProjector
from .tasks import TaskBridge

__all__ = [
    "AUR1_HEADER_SIZE", "ActiveInput", "AudioFrame", "AudioFrameKind", "AudioFrameTracker",
    "BoundedAudioQueue", "CapabilityTokens", "ClauseState", "ContextStore", "FoldedContext", "InvokeRequest",
    "MainAgentRuntime", "MainAgentSettings", "PendingTaskProposal", "PendingTrigger", "PrepareAdmissionPolicy", "Profile",
    "ObservationExecutorError", "ObservationLedger", "ObservationSecurityError", "SafeWebReader",
    "SourceRegistry", "TavilySearchExecutor", "WorkspaceListExecutor", "WorkspaceReadExecutor",
    "WorkspaceReader",
    "ProfileConflictError", "ProfileStore", "ProfileValidationError", "RendererClause", "Snapshot", "TaskBridge",
    "SnapshotBudget", "SnapshotProjector", "SoulDocument", "SpeechClause", "WorkspaceRegistry",
    "OutboundWriter", "ProtocolError", "RealtimeHub", "RendererLeasePort", "SUBPROTOCOL", "TaskProposalStatus",
    "WorkspaceRegistryError", "pack_audio_frame", "parse_audio_frame", "validate_control_message",
]
