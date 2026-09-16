"""Record opt-in, bounded live diagnostics without owning tasks, threads, or audio.

RealtimeHub owns this synchronous recorder; frozen profile scopes are shared with
Runtime and Speech. Scalar allowlists exclude provider payloads and secrets, while
capture and process identities make polling, clears, and restarts unambiguous.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

STAGES = frozenset({"connection", "input", "stt", "eou", "model", "tts", "playback", "system"})
DETAILS = frozenset({
    "connected", "accepting", "mode", "state", "active", "enabled", "reason", "code",
    "frames", "droppedFrames", "queuedFrames", "rms", "noiseFloor", "threshold", "vad",
    "speech", "confirmMs", "silenceMs", "confirmTargetMs", "silenceTargetMs",
    "text", "stablePrefix", "mutableSuffix", "stability", "probability", "elapsedMs",
    "fallbackMs", "timeoutMs", "incomplete", "pending", "running", "toolName", "cancelled",
    "silent", "accepted", "complete", "sentSamples", "consumedSamples", "queuedClauses",
    "hasTranscript", "endpointPending", "speechCandidate", "confirmedSpeech", "revision",
    "prepareCount", "firstAudio", "audioFinished", "inputActive", "workState", "status",
})
IDENTITIES = frozenset({"segmentId", "inputId", "invokeId", "sayId", "clauseId"})


def _safe(values: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key in allowed
            and (value is None or type(value) in (str, int, bool)
                 or type(value) is float and math.isfinite(value))}


@dataclass(frozen=True, slots=True)
class DebugScope:
    recorder: "DebugRecorder"
    profile_id: str | None
    generation: int
    revision: int

    @property
    def enabled(self) -> bool:
        return self.recorder.enabled and self.recorder.scope is self

    def emit(self, stage: str, name: str, *, level: str = "info", **values: Any) -> None:
        if self.enabled:
            try:
                self.recorder.emit(self, stage, name, level=level, **values)
            except Exception:
                # Diagnostics are observational: serialization failures must never interrupt audio/control.
                self.recorder.dropped += 1

    def sample_due(self, key: str, interval: float = .5) -> bool:
        if not self.enabled:
            return False
        now = time.monotonic()
        if now - self.recorder.samples.get(key, float("-inf")) < interval:
            return False
        self.recorder.samples[key] = now
        return True


class DebugRecorder:
    MAX_EVENTS = 1000
    MAX_BYTES = 2 * 1024 * 1024
    MAX_EVENT_BYTES = 32 * 1024

    def __init__(self) -> None:
        self.instance_id = str(uuid.uuid4())
        self.capture_id = ""
        self.enabled = False
        self.cursor = 0
        self.events: deque[tuple[dict[str, Any], int]] = deque()
        self.bytes = 0
        self.dropped = 0
        self.state: dict[str, dict[str, Any]] = {}
        self.samples: dict[str, float] = {}
        self.scope = DebugScope(self, None, 0, 0)

    def bind(self, profile_id: str | None, generation: int, revision: int) -> DebugScope:
        self.scope = DebugScope(self, profile_id, generation, revision)
        self.state.clear()
        self.samples.clear()
        return self.scope

    def clear(self, capture_id: str) -> None:
        self.capture_id = capture_id
        self.events.clear()
        self.bytes = self.dropped = 0
        self.samples.clear()

    def configure(self, enabled: bool, capture_id: str) -> None:
        if capture_id != self.capture_id:
            self.clear(capture_id)
        self.enabled = enabled

    def update_state(self, scope: DebugScope, stage: str, *, force: bool = False, **values: Any) -> None:
        if not scope.enabled or stage not in STAGES:
            return
        safe = _safe(values, DETAILS | IDENTITIES)
        # Snapshot strings have independent bounds; repeated transcript updates cannot grow state.
        safe = {key: value[:8000] if isinstance(value, str) else value for key, value in safe.items()}
        previous = self.state.get(stage, {})
        if any(key in safe and key in previous and previous[key] != safe[key] for key in IDENTITIES):
            previous = {}
        current = {**previous, **safe,
            "profileId": scope.profile_id, "generation": scope.generation,
            "configRevision": scope.revision}
        if not force and current == self.state.get(stage):
            return
        now = int(time.time() * 1000)
        self.state[stage] = {**current, "timestamp": now, "updatedAt": now}

    def emit(self, scope: DebugScope, stage: str, name: str, *, level: str = "info", **values: Any) -> None:
        if not scope.enabled or stage not in STAGES:
            return
        self.cursor += 1
        details = _safe(values, DETAILS)
        event = {"source": "backend", "seq": self.cursor, "instanceId": self.instance_id,
            "captureId": self.capture_id, "timestamp": int(time.time() * 1000),
            "stage": stage, "name": name[:128], "level": level if level in {"info", "warning", "error"} else "info",
            "generation": scope.generation, "configRevision": scope.revision,
            **({"profileId": scope.profile_id} if scope.profile_id else {}),
            **{key: value[:128] for key, value in values.items() if key in IDENTITIES and isinstance(value, str) and value},
            "details": details}
        def size() -> int:
            return len(json.dumps(event, ensure_ascii=False).encode("utf-8", errors="replace"))
        length = size()
        while length > self.MAX_EVENT_BYTES:
            strings = [key for key, value in details.items() if isinstance(value, str) and value]
            if not strings:
                break
            key = max(strings, key=lambda item: len(details[item]))
            details[key] = details[key][:len(details[key]) // 2]
            event["truncated"] = True
            length = size()
        self.events.append((event, length))
        self.bytes += length
        while len(self.events) > self.MAX_EVENTS or self.bytes > self.MAX_BYTES:
            self.bytes -= self.events.popleft()[1]
            self.dropped += 1
        self.update_state(scope, stage, force=True, **details, **_safe(values, IDENTITIES))

    def snapshot(self, after: int = 0, instance_id: str | None = None, capture_id: str | None = None) -> dict[str, Any]:
        restarted = instance_id is not None and instance_id != self.instance_id
        events = [{**event, "details": event["details"].copy()} for event, _ in self.events if restarted or event["seq"] > after]
        if capture_id is not None and capture_id != self.capture_id:
            events = []
        return {"instanceId": self.instance_id, "captureId": self.capture_id, "cursor": self.cursor,
            "events": events, "dropped": self.dropped + int(restarted),
            "state": {stage: state.copy() for stage, state in self.state.items()}, "enabled": self.enabled}
