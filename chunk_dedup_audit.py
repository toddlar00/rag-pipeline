"""Bounded passive evidence of actual chunk-deduplication dispositions.

Ordinals identify input occurrences, including repeated appearances of one
object. Text is the value consumed by the fingerprint call, not a snapshot of
mutable output metadata. No comparison trace or semantic-fidelity verdict is
implied. Captures observe their own thread and do not propagate to workers.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
from threading import get_ident
from types import MappingProxyType


LIMITS = MappingProxyType({
    "max_frames": 64, "max_depth": 8, "max_occurrences": 4096,
    "max_text_chars": 4096, "max_events": 16384, "max_total_chars": 262144,
})
_CAPTURE = ContextVar("chunk_dedup_capture", default=None)
_FRAME = ContextVar("chunk_dedup_frame", default=None)


class ChunkDedupCollector:
    """Single-use collector; unsupported or exhausted observation abstains."""

    def __init__(self):
        self._lifecycle = "new"
        self._thread = None
        self._frames = []
        self._omitted = self._events = self._characters = 0

    def report(self):
        if self._lifecycle != "finished":
            raise RuntimeError("chunk dedup capture has not finished")
        statuses = {frame["status"] for frame in self._frames}
        status = ("abstained" if self._omitted or "abstained" in statuses
                  else "failed" if "failed" in statuses else "complete")
        return deepcopy({
            "schema_version": 1, "kind": "chunk_dedup_observation",
            "profile": "chunk_deduplication_v1", "limits": dict(LIMITS),
            "status": status, "frames": self._frames,
            "omitted_frame_count": self._omitted,
        })


@contextmanager
def capture_chunk_dedup(collector):
    """Observe calls in this thread/context, restoring any enclosing capture."""
    if type(collector) is not ChunkDedupCollector:
        raise TypeError("a ChunkDedupCollector is required")
    if collector._lifecycle != "new":
        raise RuntimeError("chunk dedup collector is single-use")
    collector._lifecycle, collector._thread = "active", get_ident()
    tokens = ((_CAPTURE, _CAPTURE.set(collector)), (_FRAME, _FRAME.set(None)))
    try:
        yield collector
    finally:
        collector._lifecycle = "finished"
        for variable, token in reversed(tokens):
            variable.reset(token)


class _Observation:
    def __init__(self, collector, parent):
        self.collector, self.open = collector, True
        self.depth = 1 if parent is None else parent.depth + 1
        self.result_id = None
        self.record = {
            "frame_id": len(collector._frames) + 1,
            "parent_frame_id": None if parent is None else parent.record["frame_id"],
            "status": "complete", "reason": None, "exception_type": None,
            "input_count": None, "entries": [], "output_ordinals": [],
        }
        collector._frames.append(self.record)
        if self.depth > LIMITS["max_depth"]:
            self.abstain("depth_budget")

    def abstain(self, reason):
        self.record.update(status="abstained", reason=reason, entries=[], output_ordinals=[])
        self.result_id = None

    def observe(self, event, index, value):
        if (not self.open or self.collector._lifecycle != "active"
                or self.collector._thread != get_ident() or _CAPTURE.get() is not self.collector
                or _FRAME.get() is not self or self.record["status"] != "complete"):
            return value
        collector, entries = self.collector, self.record["entries"]
        if collector._events >= LIMITS["max_events"]:
            self.abstain("event_budget")
            return value
        collector._events += 1
        if event == "input":
            if index != len(entries) or self.result_id is not None:
                self.abstain("unobserved_input")
                return value
            if len(entries) >= LIMITS["max_occurrences"]:
                self.abstain("occurrence_budget")
                return value
            if type(value) is not str:
                self.abstain("unsupported_text")
                return value
            if len(value) > LIMITS["max_text_chars"]:
                self.abstain("text_budget")
                return value
            if collector._characters + len(value) > LIMITS["max_total_chars"]:
                self.abstain("character_budget")
                return value
            try:
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            except UnicodeError:
                self.abstain("unsupported_unicode")
                return value
            collector._characters += len(value)
            entries.append({"input_ordinal": index, "text": value, "text_sha256": digest,
                            "disposition": None, "retained_input_ordinal": None})
        elif event == "result":
            if (self.result_id is not None or index != len(self.record["output_ordinals"])
                    or any(entry["disposition"] is None for entry in entries)):
                self.abstain("unobserved_result")
            else:
                self.result_id = value
                self.record["input_count"] = len(entries)
        elif (not entries or index != len(entries) - 1
              or entries[index]["disposition"] is not None):
            self.abstain("unobserved_decision")
        elif event == "keep":
            entries[index].update(disposition="kept", retained_input_ordinal=index)
            self.record["output_ordinals"].append(index)
        elif (event == "remove" and type(value) is int and 0 <= value < index
              and entries[value]["disposition"] == "kept"):
            entries[index].update(disposition="removed", retained_input_ordinal=value)
        else:
            self.abstain("unobserved_decision")
        return value

    def finish(self, result):
        if self.record["status"] != "complete":
            return
        if (type(result) is not list or id(result) != self.result_id
                or len(result) != len(self.record["output_ordinals"])):
            self.abstain("unobserved_result")


def _call_core(target, original, chunks, threshold, **kwargs):
    """Use already-resolved callbacks; replacement cores keep their signature."""
    collector = _CAPTURE.get()
    if (collector is None or collector._lifecycle != "active"
            or collector._thread != get_ident()):
        return target(chunks, threshold, **kwargs)
    parent = _FRAME.get()
    if parent is not None and not parent.open:
        parent = None
    if len(collector._frames) >= LIMITS["max_frames"]:
        collector._omitted += 1
        return target(chunks, threshold, **kwargs)
    frame = _Observation(collector, parent)
    token = _FRAME.set(frame)
    try:
        if target is not original:
            frame.abstain("unsupported_core")
        if frame.record["status"] == "complete":
            result = target(chunks, threshold, **kwargs, audit_hook=frame.observe)
        else:
            result = target(chunks, threshold, **kwargs)
        frame.finish(result)
        return result
    except BaseException as exc:
        frame.record["exception_type"] = type.__dict__["__name__"].__get__(type(exc), type)[:128]
        if frame.record["status"] == "complete":
            frame.record["status"] = "failed"
        raise
    finally:
        frame.open = False
        _FRAME.reset(token)
