from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

import rag
from tools import check_architecture_inventory as inventory


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _example_repository(root: Path) -> Path:
    _write(
        root / "rag.py",
        """import os as operating
from pkg import VALUE as imported_value

PUBLIC = 1
_PRIVATE = 2

def local(a, /, b=1, *args, c, d=2, **kwargs):
    return a, b, args, c, d, kwargs

class Thing:
    def method(self, value):
        return value

def _synchronize():
    global DYNAMIC
    DYNAMIC = 3

_synchronize()

if __name__ == "__main__":
    TRANSIENT = "NEVER-LEAK-SOURCE-TEXT"
""",
    )
    _write(
        root / "consumer.py",
        """import rag as facade

def use():
    return facade.PUBLIC, facade._PRIVATE
""",
    )
    _write(
        root / "pkg" / "__init__.py",
        """from consumer import use

VALUE = use
""",
    )
    _write(
        root / "tests" / "test_facade.py",
        """import rag
from rag import _PRIVATE

def test_facade(monkeypatch):
    monkeypatch.setattr(rag, "_PRIVATE", 4)
    monkeypatch.setattr(rag.operating, "getenv", lambda _name: None)
    name = "_PRIVATE"
    monkeypatch.delattr(rag, name, raising=False)
    assert rag._PRIVATE == _PRIVATE
""",
    )
    _write(root / "tools" / "probe.py", "VALUE = 1\n")
    _git(root, "init", "-q")
    _git(root, "add", "--all")
    return root


def _module(value: dict[str, object], name: str) -> dict[str, object]:
    return next(
        item for item in value["production"]["modules"]
        if item["module"] == name
    )


def test_build_inventory_covers_metrics_graph_facade_and_consumers(tmp_path):
    root = _example_repository(tmp_path)

    value = inventory.build_inventory(root)

    assert value["tracked_sources"] == {
        "count": 5,
        "groups": [
            {
                "count": 3,
                "name": "production",
                "paths": ["consumer.py", "pkg/__init__.py", "rag.py"],
            },
            {
                "count": 1,
                "name": "tests",
                "paths": ["tests/test_facade.py"],
            },
            {
                "count": 1,
                "name": "tools",
                "paths": ["tools/probe.py"],
            },
            {"count": 0, "name": "other", "paths": []},
        ],
    }
    assert value["production"]["module_count"] == 3
    assert value["production"]["top_level_function_count"] == 3
    assert value["production"]["class_count"] == 1
    assert value["production"]["import_graph"]["cyclic_components"] == [
        ["consumer", "pkg", "rag"]
    ]

    rag_module = _module(value, "rag")
    local = next(
        item for item in rag_module["functions"]
        if item["name"] == "local"
    )
    assert local["scope"] == "top_level"
    assert local["span"][1] - local["span"][0] + 1 == 2
    assert local["arity"] == {
        "accepts_var_keyword": True,
        "accepts_var_positional": True,
        "keyword_only": 2,
        "positional_only": 1,
        "positional_or_keyword": 1,
        "required": 2,
        "total": 6,
    }
    method = next(
        item for item in rag_module["functions"]
        if item["name"] == "Thing.method"
    )
    assert method["scope"] == "class"

    facade = value["rag_facade"]
    assert "DYNAMIC" in facade["explicit_bindings"]
    assert "TRANSIENT" not in facade["explicit_bindings"]
    assert facade["import_star"] == {
        "names": ["DYNAMIC", "PUBLIC", "Thing", "imported_value", "local", "operating"],
        "uses_explicit_all": False,
    }
    assert facade["initializer_bindings"] == [{
        "initializer": "_synchronize",
        "name": "DYNAMIC",
    }]
    assert {
        item["name"] for item in facade["local_definitions"]
    } >= {"Thing", "_synchronize", "local"}

    assert [
        item["module"] for item in value["consumers"]["production"]
    ] == ["consumer"]
    consumer = value["consumers"]["production"][0]
    assert [item["name"] for item in consumer["references"]] == [
        "PUBLIC", "_PRIVATE"
    ]
    private = value["consumers"]["tests"]["private_references"]
    assert [item["name"] for item in private] == ["_PRIVATE"]
    assert set(private[0]["kinds"]) == {
        "attribute", "from_import"
    }
    assert private[0]["occurrence_count"] == 2
    seams = value["consumers"]["tests"]["monkeypatch_seams"]
    assert [item["target"] for item in seams["top_level"]] == ["_PRIVATE"]
    assert [item["target"] for item in seams["nested"]] == [
        "operating.getenv"
    ]
    assert seams["dynamic_sites"] == [{
        "line": 8,
        "operation": "delattr",
        "source": "tests/test_facade.py",
    }]


def test_inventory_bytes_are_deterministic_and_content_free(tmp_path):
    root = _example_repository(tmp_path)

    first = inventory.inventory_bytes(inventory.build_inventory(root))
    for path in root.rglob("*.py"):
        os.utime(path, None)
    second = inventory.inventory_bytes(inventory.build_inventory(root))

    assert first == second
    assert b"NEVER-LEAK-SOURCE-TEXT" not in first
    assert str(root).encode() not in first
    assert first.endswith(b"\n")
    assert json.loads(first)["analysis_model"] == inventory.ANALYSIS_MODEL


def test_refresh_check_and_drift_detection(tmp_path):
    root = _example_repository(tmp_path)
    baseline = Path("evidence/inventory.json")

    refreshed = inventory.refresh_baseline(root, baseline)

    assert inventory.check_baseline(root, baseline) == refreshed
    path = root / baseline
    assert path.read_bytes() == inventory.inventory_bytes(refreshed)

    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="differs from the committed baseline",
    ):
        inventory.check_baseline(root, baseline)

    updated = inventory.refresh_baseline(root, baseline)
    assert updated != refreshed
    assert inventory.check_baseline(root, baseline) == updated


def test_check_rejects_noncanonical_or_tampered_baseline(tmp_path):
    root = _example_repository(tmp_path)
    baseline = Path("inventory.json")
    inventory.refresh_baseline(root, baseline)
    path = root / baseline

    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert inventory.check_baseline(root, baseline)["schema_version"] == 1

    inventory.refresh_baseline(root, baseline)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(
        inventory.ArchitectureInventoryError, match="not canonical"
    ):
        inventory.check_baseline(root, baseline)

    inventory.refresh_baseline(root, baseline)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["production"]["physical_line_count"] += 1
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="physical_line_count is inconsistent",
    ):
        inventory.check_baseline(root, baseline)


def test_schema_rejects_counts_paths_order_and_graph_tampering(tmp_path):
    value = inventory.build_inventory(_example_repository(tmp_path))

    bad_count = deepcopy(value)
    bad_count["tracked_sources"]["count"] += 1
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="source counts/paths",
    ):
        inventory.inventory_bytes(bad_count)

    bad_path = deepcopy(value)
    bad_path["tracked_sources"]["groups"][0]["paths"][0] = "../escape.py"
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="safe relative POSIX",
    ):
        inventory.inventory_bytes(bad_path)

    bad_order = deepcopy(value)
    bad_order["rag_facade"]["explicit_bindings"].reverse()
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="sorted and contain no duplicates",
    ):
        inventory.inventory_bytes(bad_order)

    bad_graph = deepcopy(value)
    bad_graph["production"]["import_graph"]["cyclic_components"] = []
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="cyclic components differ",
    ):
        inventory.inventory_bytes(bad_graph)


@pytest.mark.parametrize(
    "payload, message",
    [
        ('{"schema_version": 1, "schema_version": 1}', "duplicate JSON field"),
        ('{"schema_version": NaN}', "non-standard JSON number"),
        ('{"schema_version": 1}', "inventory fields differ"),
    ],
)
def test_load_inventory_rejects_non_strict_or_incomplete_json(
    tmp_path, payload, message,
):
    path = tmp_path / "inventory.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(inventory.ArchitectureInventoryError, match=message):
        inventory.load_inventory(path)


def test_invalid_roots_sources_and_baseline_paths_fail_closed(tmp_path):
    root = _example_repository(tmp_path / "repository")

    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="Baseline path must remain inside",
    ):
        inventory.refresh_baseline(root, Path("../outside.json"))

    with pytest.raises(
        inventory.ArchitectureInventoryError, match="Repository root is invalid"
    ):
        inventory.build_inventory(tmp_path / "missing")

    not_git = tmp_path / "not-git"
    not_git.mkdir()
    with pytest.raises(
        inventory.ArchitectureInventoryError, match="git ls-files failed"
    ):
        inventory.build_inventory(not_git)

    _write(root / "broken.py", "if True print('broken')\n")
    _git(root, "add", "broken.py")
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="Cannot parse tracked Python source broken.py",
    ):
        inventory.build_inventory(root)


def test_cli_report_refresh_and_check_modes(tmp_path, capsys):
    root = _example_repository(tmp_path)
    expected_report = inventory.inventory_bytes(inventory.build_inventory(root))

    assert inventory.main(["--root", str(root), "--report"]) == 0
    report = capsys.readouterr()
    assert report.out.encode("utf-8") == expected_report
    assert json.loads(report.out)["schema_version"] == inventory.SCHEMA_VERSION
    assert report.err == ""

    arguments = [
        "--root", str(root), "--baseline", "reviewed.json", "--refresh"
    ]
    assert inventory.main(arguments) == 0
    refreshed = capsys.readouterr()
    assert "Architecture inventory refreshed:" in refreshed.out
    assert refreshed.err == ""

    assert inventory.main([
        "--root", str(root), "--baseline", "reviewed.json"
    ]) == 0
    checked = capsys.readouterr()
    assert "Architecture inventory passed:" in checked.out
    assert checked.err == ""


def test_repository_baseline_is_current_and_canonical():
    value = inventory.check_baseline(PROJECT_ROOT)
    baseline = PROJECT_ROOT / inventory.DEFAULT_BASELINE

    assert baseline.read_bytes() == inventory.inventory_bytes(value)
    assert value["production"]["import_graph"]["cyclic_components"] == []
    assert [
        item["module"] for item in value["consumers"]["production"]
    ] == ["eval", "service_search_worker", "ui"]


def test_repository_rag_static_namespace_matches_imported_module():
    value = inventory.build_inventory(PROJECT_ROOT)
    runtime_names = sorted(
        name
        for name in vars(rag)
        if not (name.startswith("__") and name.endswith("__"))
    )

    assert value["rag_facade"]["explicit_bindings"] == runtime_names
    assert value["rag_facade"]["import_star"]["names"] == sorted(
        name for name in runtime_names if not name.startswith("_")
    )
