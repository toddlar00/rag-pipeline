"""Optional reader-attempt scope for same-call OCR disposition observations.

No rendering, recognition or candidate acceptance is performed here. Compose
ExecutionRecorder outside this reader adapter to join actual raw dispatches.
The outer caller validates fixed inputs and reserves the bounded full-coverage
report envelope before starting any reader retry.
The approved composition is ExecutionRecorder.reader_factory around this
reader_factory. Enabled capture refuses a non-Guard engine before dispatch;
generic callable-time receipts are not raw OCR lineage observations.
"""
from __future__ import annotations

import math
import re

from ocr_disposition_observer import DispatchBinding, DispositionFrame


def _plan_rows(plan, operation):
    # Admit bounded selectors only. The outer operation separately validates
    # the complete source-bound plan; never traverse arbitrary nested extras.
    if type(plan) is not dict or type(plan.get("regions")) is not list or not 1 <= len(plan["regions"]) <= 20:
        raise ValueError("disposition requires a bounded explicit region plan")
    result, identifiers = [], set()
    for row in plan["regions"]:
        keys = {"region_id", "page_number", "bbox"} | ({"recipe"} if operation == "hardscan" else set())
        if type(row) is not dict or set(row) != keys:
            raise ValueError("disposition plan selector differs")
        number, identifier, bbox = row["page_number"], row["region_id"], row["bbox"]
        if (type(number) is not int or not 1 <= number <= 5000 or type(identifier) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", identifier) is None or identifier in identifiers
                or type(bbox) is not list or len(bbox) != 4
                or any(type(x) not in (int, float) or not 0 <= x <= 1 or not math.isfinite(x) for x in bbox)
                or not bbox[0] < bbox[2] or not bbox[1] < bbox[3]):
            raise ValueError("disposition plan selector is invalid")
        detached = {"page_number": number, "region_id": identifier, "bbox": list(bbox)}
        if operation == "hardscan":
            from ocr_hardscan import validate_recipe
            detached["recipe"] = validate_recipe(row["recipe"])
        result.append(detached)
        identifiers.add(identifier)
    if result != sorted(result, key=lambda row: (row["page_number"], row["region_id"])):
        raise ValueError("disposition plan requires canonical order")
    return result


class DispositionRecorder:
    """Bound one explicit operation, request generation and ordered attempts."""

    def __init__(self, operation, request_sha256, plan=None):
        if type(operation) is not str or operation not in ("pages", "regions", "hardscan"):
            raise ValueError("unsupported disposition operation")
        if operation == "pages" and plan is not None:
            raise ValueError("page dispositions do not accept a region plan")
        self.operation = operation
        self.dispatch_binding = DispatchBinding(request_sha256)
        self._rows = None if operation == "pages" else _plan_rows(plan, operation)
        self._used = set()
        self._last_region = -1

    def _begin(self, reader, number, bbox=None, recipe=None):
        if type(number) is not int or not 1 <= number <= 5000:
            raise ValueError("invalid disposition page")
        if self.operation == "pages":
            identifier = f"page-{number:05d}"
        else:
            # The API lacks region ID. Resolve the next identical fixed selector
            # by plan ordinal, not by successful-call count or geometry proximity.
            found = None
            for index, row in enumerate(self._rows):
                if index <= self._last_region or index in self._used:
                    continue
                if row["page_number"] == number and row["bbox"] == bbox and (self.operation != "hardscan" or row["recipe"] == recipe):
                    found = index
                    break
            if found is None or self.operation == "regions" and found != self._last_region + 1:
                raise ValueError("disposition retry differs from its fixed plan")
            self._last_region = found
            self._used.add(found)
            identifier = f"region-{found + 1:04d}"
        return self.dispatch_binding.begin(identifier, reader, reader.min_score)

    def reader_factory(self, reader_type):
        recorder = self

        class DispositionReader(reader_type):
            def retry(self, page_number):
                if recorder.operation != "pages":
                    raise ValueError("disposition reader operation differs")
                frame = recorder._begin(self, page_number)
                try:
                    return super().retry(page_number)
                finally:
                    recorder.dispatch_binding.end(frame)

            def retry_region(self, page_number, bbox):
                if recorder.operation != "regions":
                    raise ValueError("disposition reader operation differs")
                frame = recorder._begin(self, page_number, bbox)
                try:
                    return super().retry_region(page_number, bbox)
                finally:
                    recorder.dispatch_binding.end(frame)

            def retry_hardscan(self, page_number, bbox, recipe):
                if recorder.operation != "hardscan":
                    raise ValueError("disposition reader operation differs")
                frame = recorder._begin(self, page_number, bbox, recipe)
                try:
                    return super().retry_hardscan(page_number, bbox, recipe)
                finally:
                    recorder.dispatch_binding.end(frame)

        return DispositionReader

    def observations(self):
        if self.dispatch_binding.active is not None:
            raise ValueError("disposition observations require closed reader attempts")
        snapshots = []
        for frame in self.dispatch_binding.frames:
            try:
                value = frame.snapshot()
            except Exception:
                value = DispositionFrame.fallback_snapshot(frame)
            snapshots.append(value)
        return {"schema_version": 1, "kind": "ocr_disposition_observations", "operation": self.operation,
            "request_sha256": self.dispatch_binding.request_sha256,
            "attempts": snapshots}


__all__ = ["DispositionRecorder"]
