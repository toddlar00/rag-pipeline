"""Passive, bounded observations of actual source-wrapper cleanup calls.

This module owns observation only. It does not choose cleanup policy, import
the pipeline, authenticate supplied source context, or widen the existing core
replay profile. Pass steps and core-operation edits are separate replay layers.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
from itertools import count
from types import MappingProxyType


LIMITS = MappingProxyType({
    "max_frames": 64, "max_depth": 8, "max_input_chars": 4096,
    "max_state_chars": 16384, "max_events": 4096,
    "max_frame_trace_chars": 65536, "max_total_trace_chars": 262144,
})
_CAPTURE = ContextVar("source_cleanup_capture", default=None)
_FRAME = ContextVar("source_cleanup_frame", default=None)
_PASS = ContextVar("source_cleanup_pass", default=None)
_CORE = ContextVar("source_cleanup_core", default=None)
_CAPTURE_IDS = count(1)


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _owned_frame():
    """A copied context cannot revive a closed invocation, pass or operation."""
    frame, attempt, operation = _FRAME.get(), _PASS.get(), _CORE.get()
    if (frame is None or not frame.open or frame.collector is not _CAPTURE.get()
            or frame.collector._lifecycle != "active"):
        return None
    if attempt is not None and (not attempt.open or attempt.frame is not frame):
        return None
    if operation is not None and (
            not operation.open or operation.frame is not frame or not operation.attempt.open):
        return None
    return frame


def _current_frame():
    frame = _owned_frame()
    return frame if frame is not None and frame.active else None


def _exception_type(exc):
    # Invoke the built-in type-name descriptor directly. Even a metaclass
    # property named __name__ must not run while preserving a cleanup error.
    try:
        name = type.__dict__["__name__"].__get__(type(exc), type)
        return name[:128] if type(name) is str else None
    except BaseException:
        return None


class SourceCleanupCollector:
    """Single-use collector; exhaustion abstains without interrupting cleanup.

    Character budgets count every retained text payload, including unchanged
    input/output snapshots and context values, not merely changed envelopes.
    Fixed metadata and hashes are separately bounded by frames/events. Reports
    are detached; there is no arbitrary live observer callback.

    Trace completeness covers observed text-state transitions, not every raw
    object intermediate. Nonprimitive raw getter values are explicitly
    unobserved; existing conversions can later supply observed primitives.
    Unsupported required text states instead make the trace abstain.
    """

    def __init__(self):
        self._lifecycle = "new"
        self._capture_id = None
        self._frames = []
        self._omitted = 0
        self._events = 0
        self._characters = 0
        self._next_pass = 0
        self._next_operation = 0

    def report(self):
        if self._lifecycle != "finished":
            raise RuntimeError("source cleanup capture has not finished")
        statuses = {frame["status"] for frame in self._frames}
        status = ("abstained" if self._omitted or "abstained" in statuses
                  else "failed" if "failed" in statuses else "complete")
        return deepcopy({
            "schema_version": 1, "kind": "source_cleanup_observation",
            "profile": "source_normalization_v1", "capture_id": self._capture_id,
            "limits": dict(LIMITS), "status": status, "frames": self._frames,
            "omitted_frame_count": self._omitted,
        })


@contextmanager
def capture_source_cleanup(collector):
    """Observe actual wrapper calls in this context; restore nested captures."""
    if type(collector) is not SourceCleanupCollector:
        raise TypeError("a SourceCleanupCollector is required")
    if collector._lifecycle != "new":
        raise RuntimeError("source cleanup collector is single-use")
    collector._lifecycle = "active"
    collector._capture_id = next(_CAPTURE_IDS)
    tokens = ((_CAPTURE, _CAPTURE.set(collector)), (_FRAME, _FRAME.set(None)),
              (_PASS, _PASS.set(None)), (_CORE, _CORE.set(None)))
    try:
        yield collector
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
        collector._lifecycle = "finished"


class _Observation:
    def __init__(self, collector, text, parent):
        self.collector = collector
        self.open = True
        self.depth = 1 if parent is None else parent.depth + 1
        self.characters = 0
        self.record = {
            "frame_id": len(collector._frames) + 1,
            "parent_frame_id": None if parent is None else parent.record["frame_id"],
            "status": "complete", "reason": None, "trace_complete": True,
            "original_text": None, "original_sha256": None,
            "final_text": None, "final_sha256": None, "exception_type": None,
            "passes": [], "committed_pass_id": None, "context": [],
        }
        collector._frames.append(self.record)
        if self.depth > LIMITS["max_depth"]:
            self.abstain("depth_budget")
        elif type(text) is not str:
            self.abstain("unsupported_text")
        elif len(text) > LIMITS["max_input_chars"]:
            self.abstain("input_budget")
        else:
            snapshot = self.snapshot(text)
            if snapshot is not None:
                self.record["original_text"], self.record["original_sha256"] = snapshot

    @property
    def active(self):
        return self.open and self.collector._lifecycle == "active" and self.record["trace_complete"]

    def abstain(self, reason):
        if self.active:
            self.record.update(status="abstained", reason=reason, trace_complete=False,
                               original_text=None, original_sha256=None,
                               final_text=None, final_sha256=None,
                               passes=[], context=[], committed_pass_id=None)

    def reserve(self, characters=0):
        if not self.active:
            return False
        if (self.collector._events >= LIMITS["max_events"]
                or self.characters + characters > LIMITS["max_frame_trace_chars"]
                or self.collector._characters + characters > LIMITS["max_total_trace_chars"]):
            self.abstain("trace_budget")
            return False
        self.collector._events += 1
        self.collector._characters += characters
        self.characters += characters
        return True

    def text_ok(self, text):
        if not self.active:
            return False
        if type(text) is not str:
            self.abstain("unsupported_text")
            return False
        if len(text) > LIMITS["max_state_chars"]:
            self.abstain("state_budget")
            return False
        return True

    def snapshot(self, text):
        if not self.text_ok(text) or not self.reserve(len(text)):
            return None
        try:
            return text, _hash(text)
        except UnicodeError:
            self.abstain("unsupported_unicode")
            return None

    def envelope(self, rule, before, after):
        if not self.text_ok(before) or not self.text_ok(after):
            return None
        if before == after:
            return None
        start, old_end, new_end = 0, len(before), len(after)
        while start < min(old_end, new_end) and before[start] == after[start]:
            start += 1
        while old_end > start and new_end > start and before[old_end - 1] == after[new_end - 1]:
            old_end -= 1
            new_end -= 1
        cost = old_end - start + new_end - start
        if not self.reserve(cost):
            return None
        try:
            return {"rule_id": rule, "before_sha256": _hash(before), "after_sha256": _hash(after),
                    "before_span": [start, old_end], "after_span": [start, new_end],
                    "removed": before[start:old_end], "inserted": after[start:new_end]}
        except UnicodeError:
            self.abstain("unsupported_unicode")
            return None

    def finish(self, text, attempt):
        if self.active:
            if attempt is None or attempt.record is None or attempt.record["disposition"] != "committed":
                self.abstain("missing_commit")
            else:
                snapshot = self.snapshot(text)
                if snapshot is not None:
                    self.record["final_text"], self.record["final_sha256"] = snapshot
                    self.record["committed_pass_id"] = attempt.record["pass_id"]
        return text


@contextmanager
def _invocation(text):
    collector, parent = _CAPTURE.get(), _owned_frame()
    if collector is None or collector._lifecycle != "active":
        yield None
        return
    if len(collector._frames) >= LIMITS["max_frames"]:
        collector._omitted += 1
        frame = None
    else:
        frame = _Observation(collector, text, parent)
    tokens = ((_FRAME, _FRAME.set(frame)), (_PASS, _PASS.set(None)), (_CORE, _CORE.set(None)))
    try:
        yield frame
    except BaseException as exc:
        if frame is not None:
            frame.record["exception_type"] = _exception_type(exc)
            if frame.active:
                frame.record["status"] = "failed"
        raise
    finally:
        if frame is not None:
            frame.open = False
        for variable, token in reversed(tokens):
            variable.reset(token)


def _value(site, value):
    """Return the exact consumed object without additional conversion or reads."""
    frame = _current_frame()
    if frame is not None and frame.active:
        kind = type(value)
        observed = kind is str or kind is bool or kind is type(None) or (
            kind is int and -(2 ** 63) <= value < 2 ** 63)
        if kind is str and not frame.text_ok(value):
            return value
        cost = len(value) if kind is str else 0
        if frame.reserve(cost):
            frame.record["context"].append({"site": site, "observed": observed,
                                            "value": value if observed else None})
    return value


def _mapping(site, values):
    frame = _current_frame()
    if frame is None or not frame.active:
        return
    if type(values) is not dict:
        _value(site, values)
        return
    _value(site + ".count", len(values))
    for key, value in values.items():
        if not frame.active:
            break
        _value(site + ".key", key)
        _value(site + ".value", value)


class _Attempt:
    def __init__(self, frame, name, role, text, parent):
        self.frame, self.state, self.record = frame, text, None
        self.open = True
        if frame is not None and frame.active:
            snapshot = frame.snapshot(text)
            if snapshot is not None:
                frame.collector._next_pass += 1
                self.record = {
                    "pass_id": frame.collector._next_pass,
                    "parent_pass_id": None if parent is None or parent.record is None else parent.record["pass_id"],
                    "name": name, "role": role, "disposition": "pending",
                    "input_text": snapshot[0], "input_sha256": snapshot[1],
                    "output_text": None, "output_sha256": None, "steps": [], "operations": [],
                }
                frame.record["passes"].append(self.record)

    def finish(self, text, disposition):
        if self.open and self.record is not None and self.frame.active:
            if not any(operation["status"] == "complete" and operation["is_pass_root"]
                       for operation in self.record["operations"]):
                self.frame.abstain("missing_core")
            elif type(text) is not str or text != self.state:
                self.frame.abstain("unobserved_pass_result")
            else:
                snapshot = self.frame.snapshot(text)
                if snapshot is not None:
                    self.record.update(output_text=snapshot[0], output_sha256=snapshot[1], disposition=disposition)
        self.open = False
        return text


@contextmanager
def _pass_scope(name, role, text):
    frame = _owned_frame()
    attempt = _Attempt(frame, name, role, text, _PASS.get())
    token = _PASS.set(attempt)
    try:
        yield attempt
    except BaseException:
        if attempt.record is not None and frame.active:
            attempt.record["disposition"] = "failed"
        raise
    finally:
        attempt.open = False
        _PASS.reset(token)


def _step(rule, before, after, operation_id=None):
    attempt = _PASS.get()
    if _current_frame() is None or attempt is None or attempt.record is None:
        return after
    if type(before) is not str or before != attempt.state:
        attempt.frame.abstain("unobserved_pass_state")
        return after
    edit = attempt.frame.envelope(rule, before, after)
    if edit is not None:
        if operation_id is not None:
            edit["operation_id"] = operation_id
        attempt.record["steps"].append(edit)
    attempt.state = after
    return after


class _Operation:
    def __init__(self, frame, attempt, record):
        self.frame, self.attempt, self.record, self.open = frame, attempt, record, True


def _call_core(target, original, text, **kwargs):
    """Use the one already-resolved target, preserving replacement signatures."""
    frame, attempt, parent = _current_frame(), _PASS.get(), _CORE.get()
    if frame is None or not frame.active:
        return target(text, **kwargs)
    if target is not original:
        frame.abstain("unsupported_core")
        return target(text, **kwargs)
    if attempt is None or attempt.record is None:
        frame.abstain("missing_pass")
        return target(text, **kwargs)
    snapshot = frame.snapshot(text)
    if snapshot is None:
        return target(text, **kwargs)
    frame.collector._next_operation += 1
    operation = {"operation_id": frame.collector._next_operation,
                 "parent_operation_id": None if parent is None else parent.record["operation_id"],
                 "is_pass_root": parent is None or parent.attempt is not attempt,
                 "input_text": snapshot[0], "input_sha256": snapshot[1],
                 "output_text": None, "output_sha256": None, "status": "pending", "edits": []}
    attempt.record["operations"].append(operation)
    state = text

    def observe(rule, before, after):
        nonlocal state
        if not frame.active:
            return
        if type(before) is not str or before != state:
            frame.abstain("unobserved_core_state")
            return
        edit = frame.envelope(rule, before, after)
        if edit is not None:
            operation["edits"].append(edit)
        state = after

    owner = _Operation(frame, attempt, operation)
    token = _CORE.set(owner)
    try:
        result = target(text, **kwargs, audit_hook=observe)
    except BaseException:
        if frame.active:
            operation["status"] = "failed"
        raise
    finally:
        owner.open = False
        _CORE.reset(token)
    if frame.active:
        if type(result) is not str or result != state:
            frame.abstain("unobserved_core_result")
        else:
            snapshot = frame.snapshot(result)
            if snapshot is not None:
                operation.update(output_text=snapshot[0], output_sha256=snapshot[1], status="complete")
                if operation["is_pass_root"]:
                    _step("core_normalization", text, result, operation["operation_id"])
    return result
