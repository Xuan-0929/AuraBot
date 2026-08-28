"""暴露单 Active Profile Main Agent 的 Context、Memory、调度、语音与工具边界。

Electron 是 Profile/Soul 唯一事实源；Backend 的 ActiveProfileManager 仅按 realtime token 打开
该 Profile 的运行数据。Runtime 通过 Context、Snapshot、TaskBridge、Tavily/Workspace 工具和
SpeechService 协作，且只有 Pet 物理 ACK 能提交助手语言。包内不再提供人格文件 Store、多
Renderer 协议、诊断 transport 或 Memory lease/outbox 抽象。
"""

from .context import ClauseState, ContextStore, FoldedContext, SpeechClause
from .observations import (
    ObservationExecutorError,
    ObservationLedger,
    ObservationSecurityError,
    TavilyTools,
    WorkspaceListExecutor,
    WorkspaceReadExecutor,
    WorkspaceReader,
)
from .lifecycle import ActiveProfileManager, ProfileResources
from .memory import ProfileMemory
from .realtime import (
    CapabilityTokens,
    MicFrameQueue,
    ProtocolError,
    RealtimeHub,
    RendererPort,
    SUBPROTOCOL,
)
from .runtime import (
    ActiveInput,
    InputSegment,
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
    "ActiveInput", "CapabilityTokens", "ClauseState", "ContextStore", "FoldedContext", "InputSegment", "InvokeRequest",
    "MainAgentRuntime", "MainAgentSettings", "PendingTaskProposal", "PendingTrigger", "PrepareAdmissionPolicy",
    "ObservationExecutorError", "ObservationLedger", "ObservationSecurityError", "TavilyTools",
    "WorkspaceListExecutor", "WorkspaceReadExecutor",
    "WorkspaceReader",
    "ActiveProfileManager", "ProfileMemory", "ProfileResources", "RendererClause", "Snapshot", "TaskBridge",
    "SnapshotBudget", "SnapshotProjector", "SpeechClause", "WorkspaceRegistry",
    "MicFrameQueue", "ProtocolError", "RealtimeHub", "RendererPort", "SUBPROTOCOL", "TaskProposalStatus",
    "WorkspaceRegistryError",
]
