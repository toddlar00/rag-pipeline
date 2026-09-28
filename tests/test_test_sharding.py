"""The ``--shard INDEX/COUNT`` option partitions one collection exactly.

CI runs the Windows unit lane as three shards; together they must run every
selected test exactly once, in the original relative order.
"""

from pathlib import Path
import subprocess
import sys

import pytest

import conftest


ITEMS = [f"test_{number}" for number in range(10)]


@pytest.mark.parametrize("count", [1, 2, 3, 4, 11])
def test_shards_are_disjoint_and_cover_the_collection(count):
    shards = [conftest.shard_items(ITEMS, f"{index}/{count}")
              for index in range(1, count + 1)]

    chosen = [item for selected, _ in shards for item in selected]
    assert sorted(chosen) == sorted(ITEMS)
    assert len(chosen) == len(set(chosen))
    for selected, deselected in shards:
        assert sorted(selected + deselected) == sorted(ITEMS)
        assert selected == [item for item in ITEMS if item in selected]


def test_round_robin_spreads_neighbouring_tests():
    assert conftest.shard_items(ITEMS, "2/3") == (
        ["test_1", "test_4", "test_7"],
        ["test_0", "test_2", "test_3", "test_5", "test_6", "test_8",
         "test_9"])


@pytest.mark.parametrize("value", ["0/3", "4/3", "1/0", "3", "a/b", "1/3/1",
                                   " 1/3", "01/3"])
def test_invalid_shards_are_usage_errors(value):
    with pytest.raises(pytest.UsageError):
        conftest.shard_items(ITEMS, value)


def test_option_selects_one_shard_after_keyword_deselection(tmp_path):
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('n', range(7))\n"
        "def test_kept(n):\n    pass\n\n"
        "def test_dropped():\n    pass\n",
        encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        Path(conftest.__file__).read_text(encoding="utf-8"), encoding="utf-8")

    def run(*arguments):
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "--collect-only", "-k", "kept", *arguments],
            cwd=tmp_path, capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return [line.split("::", 1)[1] for line in completed.stdout.splitlines()
                if "::" in line]

    everything = run()
    shards = [run("--shard", f"{index}/3") for index in (1, 2, 3)]

    assert everything == [f"test_kept[{n}]" for n in range(7)]
    assert shards == [everything[0::3], everything[1::3], everything[2::3]]
