"""Record launch-gated, bounded live diagnostics and fixture identity without owning tasks, threads, or audio.

RealtimeHub owns this synchronous recorder; frozen profile scopes are shared with
Runtime and Speech. Scalar allowlists exclude provider payloads and secrets, while
capture and process identities make polling, clears, and restarts unambiguous.
AcousticWindow aggregates scalar frame facts on the existing microphone task;
capture epochs fence partial windows across clears and enable/disable transitions.
Passing-frame statistics follow the coordinator's current gate; onset resets are VAD-only,
while independent RMS measurements and legacy reset counters remain available for comparison.
Speech-stream milestones admit only model names, character counts and flush reasons, never raw deltas.
Realtime observations admit bounded identity and timing scalars through the same recorder;
no provider payloads or additional collectors participate in speech control.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

STAGES = frozenset({"connection", "input", "stt", "eou", "model", "tts", "playback", "system"})
DETAILS = frozenset({
    "connected", "accepting", "mode", "state", "active", "enabled", "reason", "code",
    "frames", "droppedFrames", "queuedFrames", "rms", "noiseFloor", "threshold", "vad",
    "speech", "onsetGate", "confirmMs", "silenceMs", "confirmTargetMs", "silenceTargetMs",
    "text", "stablePrefix", "mutableSuffix", "stability", "probability", "elapsedMs",
    "fallbackMs", "timeoutMs", "incomplete", "pending", "running", "toolName", "cancelled",
    "silent", "accepted", "complete", "sentSamples", "consumedSamples", "queuedClauses",
    "hasTranscript", "endpointPending", "speechCandidate", "confirmedSpeech", "revision",
    "prepareCount", "firstAudio", "audioFinished", "inputActive", "workState", "status",
    "windowMs", "windowFrames", "vadPassFrames", "rmsPassFrames", "qualifiedFrames",
    "maxQualifiedRunMs", "rmsMin", "rmsMax", "thresholdMin", "thresholdMax", "maxConfirmMs",
    "resetRmsCount", "resetVadCount", "resetBothCount", "resetUnavailableCount",
    "lastResetReason", "lastResetFromMs", "partial", "vadStatus", "vadReason",
    "vadWarmupFrames", "vadUnavailableFrames", "vadErrorFrames", "vadThreshold",
    "operation", "finalizing", "exceptionType",
    "charCount", "flushReason", "modelName", "debug", "lockMemory", "memoryFixture",
    "connectionId", "responseId", "questionId", "eventId", "itemId", "callId", "providerCode",
    "sentFrames", "sentBytes", "receivedBytes", "droppedEvents", "firstAudioMs",
    "playbackWaitMs", "contextWaitMs", "audioMs", "hasAudio", "eventKind", "count",
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
        self.epoch = 0

    def bind(self, profile_id: str | None, generation: int, revision: int) -> DebugScope:
        self.epoch += 1
        self.scope = DebugScope(self, profile_id, generation, revision)
        self.state.clear()
        self.samples.clear()
        return self.scope

    def clear(self, capture_id: str) -> None:
        self.epoch += 1
        self.capture_id = capture_id
        self.events.clear()
        self.bytes = self.dropped = 0
        self.samples.clear()

    def configure(self, enabled: bool, capture_id: str) -> None:
        if enabled != self.enabled:
            self.epoch += 1
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


class AcousticWindow:
    """Observe frame decisions with constant memory and no scheduling or audio retention."""

    def __init__(self, scope: DebugScope | None, clock: Callable[[], float]) -> None:
        self.scope, self.clock = scope, clock
        self.epoch = -1
        self.started: float | None = None
        self.stats: dict[str, Any] = {}
        self.last: dict[str, Any] = {}
        self.run_ms = 0
        self.vad_state: tuple[str, str | None] | None = None

    def _current(self) -> bool:
        scope = self.scope
        if scope is None or not scope.enabled or self.epoch != scope.recorder.epoch:
            self.started, self.stats, self.last, self.run_ms = None, {}, {}, 0
            self.vad_state = None
            self.epoch = scope.recorder.epoch if scope is not None else -1
        return scope is not None and scope.enabled

    def observe(self, *, frame_ms: int, before_confirm_ms: int, was_active: bool,
                **facts: Any) -> None:
        if not self._current():
            return
        now = self.clock()
        if self.started is None:
            self.started = now
        if not self.stats:
            self.stats = dict.fromkeys(("windowFrames", "vadPassFrames", "rmsPassFrames",
                "qualifiedFrames", "maxQualifiedRunMs", "maxConfirmMs", "resetRmsCount",
                "resetVadCount", "resetBothCount", "resetUnavailableCount", "vadWarmupFrames",
                "vadUnavailableFrames", "vadErrorFrames"), 0)
            self.stats.update(rmsMin=facts["rms"], rmsMax=facts["rms"],
                thresholdMin=facts["threshold"], thresholdMax=facts["threshold"],
                lastResetReason=None, lastResetFromMs=None)
        stats = self.stats
        vad = facts["vad"]
        vad_pass = vad is not None and vad >= facts["vadThreshold"]
        rms_pass = facts["rms"] >= facts["threshold"]
        qualified = facts["speech"]
        stats["windowFrames"] += 1
        stats["vadPassFrames"] += int(vad_pass)
        stats["rmsPassFrames"] += int(rms_pass)
        stats["qualifiedFrames"] += int(qualified)
        self.run_ms = self.run_ms + frame_ms if qualified else 0
        stats["maxQualifiedRunMs"] = max(stats["maxQualifiedRunMs"], self.run_ms)
        stats["maxConfirmMs"] = max(stats["maxConfirmMs"], facts["confirmMs"])
        for key in ("rms", "threshold"):
            stats[key + "Min"] = min(stats[key + "Min"], facts[key])
            stats[key + "Max"] = max(stats[key + "Max"], facts[key])
        if not was_active and before_confirm_ms > 0 and facts["confirmMs"] == 0:
            if vad is None:
                field, reason = "resetUnavailableCount", "vad_unavailable"
            else:
                field, reason = "resetVadCount", "vad_below_threshold"
            stats[field] += 1
            stats.update(lastResetReason=reason, lastResetFromMs=before_confirm_ms)
        status = facts["vadStatus"]
        field = {"warming_up": "vadWarmupFrames", "unavailable": "vadUnavailableFrames",
                 "inference_failed": "vadErrorFrames"}.get(status)
        if field:
            stats[field] += 1
        state = (status, facts["vadReason"])
        if state != self.vad_state:
            self.vad_state = state
            self.scope.emit("input", "vad_status", vadStatus=status, vadReason=state[1],
                segmentId=facts.get("segmentId"),
                level="warning" if field and status != "warming_up" else "info")
        self.last = facts
        if now - self.started >= .5:
            self.flush(partial=False)

    def flush(self, *, partial: bool = True) -> None:
        if not self._current() or not self.stats:
            return
        now = self.clock()
        self.scope.emit("input", "acoustics", **self.last, **self.stats,
            windowMs=max(0.0, (now - self.started) * 1000), partial=partial)
        self.started = None if partial else now
        self.stats, self.last, self.run_ms = {}, {}, 0
