"""Batch framing must not weaken copied HEAD/source correspondence."""
import hashlib

import pytest

from tools.qualification_git import verify_head_blobs


LIMIT = 8 * 1024 * 1024


def _inputs(blobs):
    digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in blobs.items()}
    sizes = {name: len(raw) for name, raw in blobs.items()}
    objects = {name: hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw,
                                 usedforsecurity=False).hexdigest() for name, raw in blobs.items()}
    return list(blobs), digests, sizes, objects


def _record(raw, oid):
    return oid.encode() + b" blob " + str(len(raw)).encode() + b"\n" + raw + b"\n"


def test_binary_empty_and_unicode_paths_preserve_request_order_and_exact_bytes():
    blobs = {"space name.py": b"\0\n\r\xfftext\n", "empty.py": b"", "caf\u00e9.py": b"end"}
    paths, digests, sizes, objects = _inputs(blobs)
    seen = []

    def git(root, *args, data=None):
        seen.append((root, args, data))
        return b"".join(_record(raw, objects[name]) for name, raw in blobs.items())

    assert verify_head_blobs("copy", paths, digests, sizes, objects, git=git) == 1
    assert seen == [("copy", ("cat-file", "--batch"),
                     "HEAD:space name.py\nHEAD:empty.py\nHEAD:caf\u00e9.py\n".encode())]


@pytest.mark.parametrize("corruption", [
    "wrong_oid", "tree", "wrong_size", "leading_zero", "signed_size", "oversized_size",
    "missing_header", "long_header", "payload", "truncated", "delimiter", "missing_record",
    "extra_record", "trailing", "reordered", "stdout_limit",
])
def test_bad_batch_never_qualifies_or_falls_back(corruption):
    blobs = {"a.py": b"abc", "b.py": b"xyz"}
    paths, digests, sizes, objects = _inputs(blobs)
    first, second = (_record(blobs[name], objects[name]) for name in paths)
    mutations = {
        "wrong_oid": first.replace(objects["a.py"].encode(), b"0" * 40, 1) + second,
        "tree": first.replace(b" blob ", b" tree ", 1) + second,
        "wrong_size": first.replace(b" blob 3\n", b" blob 4\n", 1) + second,
        "leading_zero": first.replace(b" blob 3\n", b" blob 03\n", 1) + second,
        "signed_size": first.replace(b" blob 3\n", b" blob +3\n", 1) + second,
        "oversized_size": first.replace(b" blob 3\n", b" blob 99999999999999999999\n", 1) + second,
        "missing_header": b"a.py missing\n",
        "long_header": b"x" * 64 + b"\n" + first + second,
        "payload": first.replace(b"abc\n", b"abd\n", 1) + second,
        "truncated": first[:-2],
        "delimiter": first[:-1] + b"!" + second,
        "missing_record": first,
        "extra_record": first + second + second,
        "trailing": first + second + b"\n",
        "reordered": second + first,
    }
    calls = []

    def git(root, *args, data=None):
        calls.append(args)
        return b"x" * (LIMIT + 1) if corruption == "stdout_limit" else mutations[corruption]

    with pytest.raises(RuntimeError):
        verify_head_blobs("copy", paths, digests, sizes, objects, git=git)
    assert calls == [("cat-file", "--batch")]


@pytest.mark.parametrize("size,command", [
    (LIMIT - 64, "cat-file"), (LIMIT - 63, "show"), (LIMIT, "show"),
])
def test_output_boundary_preserves_legacy_single_blob_admission(size, command):
    blobs = {"before.py": b"", "large.py": b"x" * size, "after.py": b""}
    paths, digests, sizes, objects = _inputs(blobs)
    calls = []

    def git(root, *args, data=None):
        calls.append((args, data))
        if args[0] == "show":
            return blobs[args[1].removeprefix("HEAD:")]
        names = [line.removeprefix("HEAD:") for line in data.decode().splitlines()]
        response = b"".join(_record(blobs[name], objects[name]) for name in names)
        assert len(response) <= LIMIT
        return response

    assert verify_head_blobs("copy", paths, digests, sizes, objects, git=git) == 3
    assert [args[0] for args, _ in calls] == ["cat-file", command, "cat-file"]
    assert calls[0][1] == b"HEAD:before.py\n"
    assert calls[-1][1] == b"HEAD:after.py\n"


def test_multiple_small_records_pack_within_one_output_allowance():
    blobs = {"a.py": b"x" * (LIMIT - 128), "b.py": b"", "c.py": b""}
    paths, digests, sizes, objects = _inputs(blobs)
    requests = []

    def git(root, *args, data=None):
        requests.append(data)
        names = [line.removeprefix("HEAD:") for line in data.decode().splitlines()]
        return b"".join(_record(blobs[name], objects[name]) for name in names)

    assert verify_head_blobs("copy", paths, digests, sizes, objects, git=git) == 2
    assert requests == [b"HEAD:a.py\nHEAD:b.py\n", b"HEAD:c.py\n"]


@pytest.mark.parametrize("size", [-1, True, 3.5, LIMIT + 1])
def test_unadmitted_size_stops_before_git(size):
    def git(*args, **kwargs):
        pytest.fail("unadmitted size reached Git")

    with pytest.raises(RuntimeError, match="Git output exceeds bound"):
        verify_head_blobs("copy", ["a.py"], {}, {"a.py": size}, {}, git=git)


def test_git_failure_stops_later_batches_without_retry_or_fallback():
    calls = []

    def git(root, *args, data=None):
        calls.append((args, data))
        raise RuntimeError("Git operation failed: cat-file")

    with pytest.raises(RuntimeError, match="Git operation failed: cat-file"):
        verify_head_blobs("copy", ["a.py", "large.py"], {},
                          {"a.py": 0, "large.py": LIMIT}, {}, git=git)
    assert calls == [(("cat-file", "--batch"), b"HEAD:a.py\n")]


def test_corrupt_singleton_fallback_stops_without_later_calls():
    calls = []

    def git(root, *args, data=None):
        calls.append((args, data))
        return b"corrupted source"

    expected = hashlib.sha256(b"x" * LIMIT).hexdigest()
    with pytest.raises(RuntimeError, match="synthetic HEAD source bytes differ"):
        verify_head_blobs("copy", ["large.py", "later.py"], {"large.py": expected},
                          {"large.py": LIMIT, "later.py": 0}, {}, git=git)
    assert calls == [(("show", "HEAD:large.py"), None)]
