"""Standard-library operational counters shared by indexing and drills.

The module owns only bounded, content-free measurements. It deliberately does
not know about vector clients, corpus records, paths, or model payloads.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol


class WorkerFuture(Protocol):
    """The small future surface required by the bounded queue producer."""

    def done(self) -> bool: ...

    def result(self): ...


@dataclass
class QueueBackpressureMetrics:
    """Thread-safe counters for admitted bounded-queue work."""

    put_count: int = field(default=0, init=False)
    saturation_events: int = field(default=0, init=False)
    wait_ms: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False)

    def record_admission(self, *, saturation_events: int,
                         wait_ms: float) -> None:
        if (isinstance(saturation_events, bool)
                or not isinstance(saturation_events, int)
                or saturation_events < 0):
            raise ValueError("queue saturation events must be non-negative")
        if (isinstance(wait_ms, bool)
                or not isinstance(wait_ms, (int, float))
                or not math.isfinite(float(wait_ms)) or wait_ms < 0):
            raise ValueError("queue wait must be a finite non-negative number")
        with self._lock:
            next_wait_ms = self.wait_ms + float(wait_ms)
            if not math.isfinite(next_wait_ms):
                raise ValueError("cumulative queue wait must remain finite")
            next_put_count = self.put_count + 1
            next_saturation_events = (
                self.saturation_events + saturation_events)
            self.put_count = next_put_count
            self.saturation_events = next_saturation_events
            self.wait_ms = next_wait_ms

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "queue_put_count": self.put_count,
                "queue_saturation_events": self.saturation_events,
                "queue_wait_ms": round(self.wait_ms, 3),
            }


def _monotonic_value(clock) -> float:
    value = clock()
    if (isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))):
        raise ValueError("queue monotonic clock must be finite")
    return float(value)


def put_unless_worker_failed(
        work_queue: queue.Queue, item, worker_future: WorkerFuture, *,
        metrics: QueueBackpressureMetrics | None = None,
        poll_interval: float = 0.1,
        monotonic_clock=time.monotonic) -> None:
    """Admit one item with bounded polling and observable backpressure."""
    if (isinstance(poll_interval, bool)
            or not isinstance(poll_interval, (int, float))
            or not math.isfinite(float(poll_interval))
            or poll_interval <= 0):
        raise ValueError("queue poll interval must be positive and finite")
    if metrics is not None and not isinstance(
            metrics, QueueBackpressureMetrics):
        raise TypeError("metrics must be QueueBackpressureMetrics")
    started = _monotonic_value(monotonic_clock)
    saturation_events = 0
    while True:
        if worker_future.done():
            worker_future.result()
            raise RuntimeError(
                "Queue worker exited before accepting all work")
        try:
            work_queue.put(item, timeout=float(poll_interval))
        except queue.Full:
            saturation_events += 1
            continue
        if metrics is not None:
            try:
                finished = _monotonic_value(monotonic_clock)
                wait_ms = max(0.0, (finished - started) * 1000)
            except Exception:
                # The work is already admitted. A broken observation clock
                # must not turn that physical success into a caller-visible
                # failure or suppress the exact admission/saturation counts.
                wait_ms = 0.0
            metrics.record_admission(
                saturation_events=saturation_events,
                wait_ms=wait_ms,
            )
        return


__all__ = [
    "QueueBackpressureMetrics",
    "WorkerFuture",
    "put_unless_worker_failed",
]
