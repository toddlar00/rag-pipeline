"""Hash-lock parsing is cached by content without sharing mutable results.

``_lock_records`` is a pure function of the lock bytes; receipt capture and
archive validation call it for the same lock many times.  The parse is
cached, but every caller must still get its own containers, and invalid
locks must keep failing.  All lock text is synthetic.
"""

import pytest

import ocr_experiment_runtime as runtime


HASH = "sha256:" + "a" * 64
LOCK = (
    "# synthetic lock\n"
    f"alpha==1.0 \\\n    --hash={HASH}\n"
    f"beta==2.0 ; python_version >= \"3.10\" \\\n    --hash={HASH}\n"
    f"alpha==1.1 ; sys_platform == \"win32\" \\\n    --hash={HASH}\n"
).encode("utf-8")


def test_repeated_parses_are_equal_but_never_share_containers():
    runtime._parsed_lock_records.cache_clear()

    first = runtime._lock_records(LOCK)
    first["alpha"].append(("9.9", ""))
    first["gamma"] = []
    second = runtime._lock_records(bytearray(LOCK))

    assert list(second) == ["alpha", "beta"]
    assert second["alpha"] == [("1.0", ""), ("1.1", 'sys_platform == "win32"')]
    assert second["beta"] == [("2.0", 'python_version >= "3.10"')]
    assert second["alpha"] is not first["alpha"]
    assert runtime._parsed_lock_records.cache_info().hits == 1


@pytest.mark.parametrize("raw", [b"", b"alpha==1.0\n", b"\xff\xfe"])
def test_invalid_locks_fail_every_time(raw):
    for _ in range(2):
        with pytest.raises(ValueError):
            runtime._lock_records(raw)
