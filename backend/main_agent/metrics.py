"""Keep dependency-free bounded Main Agent lifecycle and latency telemetry.

``LifecycleMetrics`` stores only named counters and numeric timing summaries in small LRU maps;
individual samples are bounded deques and no transcript, prompt, Memory content, provider payload, or
high-frequency log body is retained.  Runtime, Memory worker, and FastAPI composition share this one
process-local sink, while the optional metrics route returns a read-only defensive snapshot.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from typing import Any


class LifecycleMetrics:
    """Bounded counters and timing aggregates safe for the realtime process."""

    def __init__(self, *, max_series: int = 64, max_samples: int = 128) -> None:
        if max_series < 1 or max_samples < 1:
            raise ValueError("metric bounds must be positive")
        self.max_series = max_series
        self.max_samples = max_samples
        self._counters: OrderedDict[str, int] = OrderedDict()
        self._timings: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def increment(self, name: str, value: int = 1) -> None:
        if not isinstance(name, str) or not name or isinstance(value, bool):
            raise ValueError("counter requires a name and integer value")
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + int(value)
            self._counters.move_to_end(name)
            while len(self._counters) > self.max_series:
                self._counters.popitem(last=False)

    def observe(self, name: str, seconds: float) -> None:
        if not isinstance(name, str) or not name or seconds < 0:
            raise ValueError("timing requires a name and non-negative seconds")
        with self._lock:
            samples = self._timings.get(name)
            if samples is None:
                samples = deque(maxlen=self.max_samples)
                self._timings[name] = samples
            samples.append(float(seconds))
            self._timings.move_to_end(name)
            while len(self._timings) > self.max_series:
                self._timings.popitem(last=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            timings = {
                name: {
                    "samples": len(values),
                    "lastMs": round(values[-1] * 1_000, 3) if values else 0.0,
                    "maxMs": round(max(values) * 1_000, 3) if values else 0.0,
                    "meanMs": round(sum(values) / len(values) * 1_000, 3) if values else 0.0,
                }
                for name, values in self._timings.items()
            }
        return {"counters": counters, "timings": timings}


__all__ = ["LifecycleMetrics"]
