"""Git blob verification for future isolated qualification copies.

The supplied Git runner retains its 8 MiB accepted-stdout limit and timeout.
Inputs come from the checked copy cohort, raw-object creation and source reads.
Completed qualification helpers and evidence are never rewritten by this module.
"""
from __future__ import annotations

import hashlib
import re


def verify_head_blobs(root, paths, source_files, sizes, object_ids, *, git):
    """Verify ordered HEAD blobs in bounded batches without changing Git limits."""
    limit, overhead = 8 * 1024 * 1024, 64
    calls = 0

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    def verify_batch(batch):
        raw = git(root, "cat-file", "--batch",
                  data="".join("HEAD:" + name + "\n" for name in batch).encode("utf-8"))
        require(len(raw) <= limit, "Git output exceeds bound")
        offset = 0
        for name in batch:
            end = raw.find(b"\n", offset, offset + overhead)
            require(end != -1, "synthetic HEAD batch header is missing or oversized")
            header = re.fullmatch(rb"([0-9a-f]{40}) blob (0|[1-9][0-9]{0,7})", raw[offset:end])
            require(header is not None and header[1].decode("ascii") == object_ids[name]
                    and int(header[2]) == sizes[name], "synthetic HEAD batch identity/type/size differs")
            start, stop = end + 1, end + 1 + sizes[name]
            require(raw[stop:stop + 1] == b"\n", "synthetic HEAD batch payload is truncated or unterminated")
            require(hashlib.sha256(memoryview(raw)[start:stop]).hexdigest() == source_files[name],
                    "synthetic HEAD source bytes differ")
            offset = stop + 1
        require(offset == len(raw), "synthetic HEAD batch has extra output")

    batch, expected_bytes = [], 0
    for name in paths:
        size = sizes[name]
        require(type(size) is int and 0 <= size <= limit, "Git output exceeds bound")
        if batch and expected_bytes + size + overhead > limit:
            verify_batch(batch)
            calls += 1
            batch, expected_bytes = [], 0
        if size + overhead > limit:
            # Preserve the old admission of an individual blob up to exactly
            # 8 MiB: batch framing would otherwise consume its remaining room.
            require(hashlib.sha256(git(root, "show", "HEAD:" + name)).hexdigest() == source_files[name],
                    "synthetic HEAD source bytes differ")
            calls += 1
        else:
            batch.append(name)
            expected_bytes += size + overhead
    if batch:
        verify_batch(batch)
        calls += 1
    return calls
