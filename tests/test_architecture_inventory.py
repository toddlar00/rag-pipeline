from __future__ import annotations

import ast
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
        """import logging
import os as operating
from pkg import VALUE as imported_value

PUBLIC = 1
_PRIVATE = 2
log = logging.getLogger(__name__)
_llm_runtime = object()

def local(a: int, /, b: str = "one", *args: object,
          c: bool, d: float = 2.0, **kwargs: object) -> tuple:
    return a, b, args, c, d, kwargs

def _call_llm_result():
    return _llm_runtime

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

facade_export = facade

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
        """import consumer as layer
import rag
from rag import _PRIVATE

alias = layer.facade

def test_facade(monkeypatch):
    monkeypatch.setattr(rag, "_PRIVATE", 4)
    monkeypatch.setattr(rag.operating, "getenv", lambda _name: None)
    monkeypatch.setattr(layer.facade, "PUBLIC", 4)
    name = "_PRIVATE"
    monkeypatch.delattr(rag, name, raising=False)
    alias.PUBLIC = 5
    del alias._PRIVATE
    rag._llm_runtime = object()
    del rag._llm_runtime
    assert rag._PRIVATE == _PRIVATE

def shadow(rag):
    return rag._SHADOWED
""",
    )
    _write(
        root / "tests" / "test_literal_patch.py",
        """from unittest.mock import patch

def test_literal(monkeypatch):
    with patch("rag.PUBLIC", 2):
        pass
    monkeypatch.setattr("rag._PRIVATE", 4)
""",
    )
    _write(root / "tools" / "probe.py", "import consumer\nVALUE = consumer\n")
    _write(
        root / "scripts" / "run.py",
        """import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import consumer

PLUGIN = importlib.import_module("pkg")
""",
    )
    _git(root, "init", "-q")
    _git(root, "add", "--all")
    return root


def _module(value: dict[str, object], name: str) -> dict[str, object]:
    return next(
        item for item in value["source_architecture"]["modules"]
        if item["module"] == name
    )


def test_build_inventory_covers_metrics_graph_facade_and_consumers(tmp_path):
    root = _example_repository(tmp_path)

    value = inventory.build_inventory(root)

    assert value["tracked_sources"] == {
        "count": 7,
        "groups": [
            {
                "count": 3,
                "name": "production",
                "paths": ["consumer.py", "pkg/__init__.py", "rag.py"],
            },
            {
                "count": 2,
                "name": "tests",
                "paths": [
                    "tests/test_facade.py",
                    "tests/test_literal_patch.py",
                ],
            },
            {
                "count": 1,
                "name": "tools",
                "paths": ["tools/probe.py"],
            },
            {"count": 1, "name": "other", "paths": ["scripts/run.py"]},
        ],
    }
    architecture = value["source_architecture"]
    assert architecture["module_count"] == 5
    assert architecture["top_level_function_count"] == 4
    assert architecture["class_count"] == 1
    assert architecture["source_groups"] == ["production", "tools", "other"]
    assert architecture["import_graph"]["cyclic_components"] == [
        ["consumer", "pkg", "rag"]
    ]
    script_edges = _module(value, "scripts.run")["import_edges"]
    assert script_edges == [
        {
            "evidence": [{"context": "type_only", "origin": "static"}],
            "target": "consumer",
        },
        {
            "evidence": [{"context": "runtime", "origin": "dynamic"}],
            "target": "pkg",
        },
    ]

    rag_module = _module(value, "rag")
    local = next(
        item for item in rag_module["functions"]
        if item["name"] == "local"
    )
    assert local["scope"] == "top_level"
    assert set(local) == {"kind", "name", "scope", "signature_sha256"}
    assert len(local["signature_sha256"]) == 64
    assert len(rag_module["definition_locations_sha256"]) == 64
    method = next(
        item for item in rag_module["functions"]
        if item["name"] == "Thing.method"
    )
    assert method["scope"] == "class"

    owner = value["rag_source_owner"]
    assert "DYNAMIC" in owner["explicit_bindings"]
    assert "TRANSIENT" not in owner["explicit_bindings"]
    assert owner["import_star"] == {
        "names": [
            "DYNAMIC", "PUBLIC", "Thing", "imported_value", "local", "log",
            "logging", "operating",
        ],
        "uses_explicit_all": False,
    }
    assert owner["initializer_bindings"] == [{
        "assignment_line": 23,
        "initializer": "_synchronize",
        "line": 25,
        "name": "DYNAMIC",
    }]
    assert {
        item["name"] for item in owner["local_definitions"]
    } >= {"Thing", "_synchronize", "local"}
    assert owner["consumer_exports"] == [{
        "bindings": ["facade", "facade_export"],
        "module": "consumer",
    }]
    assert all(value["rag_runtime_contract"]["behavior"].values())
    assert value["rag_runtime_contract"]["logger_name"] == "rag"

    assert [
        item["module"] for item in value["consumers"]["production"]
    ] == ["consumer"]
    consumer = value["consumers"]["production"][0]
    assert [item["name"] for item in consumer["references"]] == [
        "PUBLIC", "_PRIVATE"
    ]
    private = value["consumers"]["tests"]["private_read_references"]
    assert [item["name"] for item in private] == ["_PRIVATE"]
    assert set(private[0]["kinds"]) == {
        "attribute", "from_import"
    }
    assert private[0]["occurrence_count"] == 2
    seams = value["consumers"]["tests"]["monkeypatch_seams"]
    assert [item["target"] for item in seams["top_level"]] == [
        "PUBLIC", "_PRIVATE",
    ]
    public_seam = seams["top_level"][0]
    assert public_seam["operations"] == ["patch", "setattr"]
    assert public_seam["source_counts"] == [
        {"count": 1, "path": "tests/test_facade.py"},
        {"count": 1, "path": "tests/test_literal_patch.py"},
    ]
    assert [item["target"] for item in seams["nested"]] == [
        "operating.getenv"
    ]
    assert seams["dynamic_sites"] == [{
        "line": 12,
        "operation": "delattr",
        "source": "tests/test_facade.py",
    }]
    mutations = value["consumers"]["tests"]["direct_mutations"]
    assert [item["target"] for item in mutations["top_level"]] == [
        "PUBLIC", "_PRIVATE", "_llm_runtime",
    ]
    assert "_SHADOWED" not in {item["name"] for item in private}


def test_inventory_bytes_are_deterministic_and_content_free(tmp_path):
    root = _example_repository(tmp_path)

    first = inventory.inventory_bytes(inventory.build_inventory(root))
    for path in root.rglob("*.py"):
        os.utime(path, None)
    second = inventory.inventory_bytes(inventory.build_inventory(root))

    assert first == second
    assert b"NEVER-LEAK-SOURCE-TEXT" not in first
    assert str(root).encode() not in first
    assert first.startswith(b"{\n  ")
    assert b'\n  "source_architecture": {' in first
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
    assert inventory.check_baseline(root, baseline)["schema_version"] == 3

    inventory.refresh_baseline(root, baseline)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(
        inventory.ArchitectureInventoryError, match="not canonical"
    ):
        inventory.check_baseline(root, baseline)

    inventory.refresh_baseline(root, baseline)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["source_architecture"]["physical_line_count"] += 1
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
        match="tracked_sources.count is inconsistent",
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
    bad_order["rag_source_owner"]["explicit_bindings"].reverse()
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="sorted and contain no duplicates",
    ):
        inventory.inventory_bytes(bad_order)

    bad_graph = deepcopy(value)
    bad_graph["source_architecture"]["import_graph"]["cyclic_components"] = []
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="cyclic components differ",
    ):
        inventory.inventory_bytes(bad_graph)

    bad_origin = deepcopy(value)
    _module(bad_origin, "scripts.run")["import_edges"][0]["evidence"][0][
        "origin"
    ] = "guessed"
    with pytest.raises(
        inventory.ArchitectureInventoryError, match=r"origin is invalid"
    ):
        inventory.inventory_bytes(bad_origin)

    bad_domain = deepcopy(value)
    bad_domain["hash_domains"]["static_signature"] = "unreviewed"
    with pytest.raises(
        inventory.ArchitectureInventoryError, match=r"hash_domains is unsupported"
    ):
        inventory.inventory_bytes(bad_domain)

    bad_contract = deepcopy(value)
    bad_contract["rag_runtime_contract"]["callables"][0][
        "contract_sha256"
    ] = "short"
    with pytest.raises(
        inventory.ArchitectureInventoryError, match=r"contract_sha256"
    ):
        inventory.inventory_bytes(bad_contract)


def test_signature_contract_detects_same_span_api_drift(tmp_path):
    root = _example_repository(tmp_path)
    before = _module(inventory.build_inventory(root), "rag")
    source = (root / "rag.py").read_text(encoding="utf-8")
    source = source.replace(
        'def local(a: int, /, b: str = "one", *args: object,',
        'def local(a: bytes, /, b: str = "two", *items: object,',
    ).replace(
        "return a, b, args, c, d, kwargs",
        "return a, b, items, c, d, kwargs",
    )
    _write(root / "rag.py", source)
    after = _module(inventory.build_inventory(root), "rag")
    before_local = next(item for item in before["functions"] if item["name"] == "local")
    after_local = next(item for item in after["functions"] if item["name"] == "local")

    assert before["definition_locations_sha256"] == after[
        "definition_locations_sha256"
    ]
    assert before_local["signature_sha256"] != after_local["signature_sha256"]


@pytest.mark.parametrize(
    "statement",
    [
        '__all__ = ["PUBLIC"]\n__all__ += ["Thing"]\n',
        'if PUBLIC:\n    __all__ = ["PUBLIC"]\n',
        '__all__ = tuple(["PUBLIC"])\n',
        '(__all__ := ["PUBLIC"])\n',
        '__all__ = ["PUBLIC"]\n__all__.append("Thing")\n',
        '__all__ = ["PUBLIC"]\n__all__[:] = ["Thing"]\n',
        '__all__ = ["PUBLIC"]\ndel __all__[0]\n',
        '__all__ = ["PUBLIC"]\nalias = __all__\nalias.append("Thing")\n',
        '__all__ = ["PUBLIC"]\nmutate = __all__.append\nmutate("Thing")\n',
        '__all__ = ["PUBLIC"]\nalias = __all__\nalias[:] += ["Thing"]\n',
    ],
)
def test_ambiguous_all_fails_closed(tmp_path, statement):
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(statement)

    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match=r"rag\.__all__ must be one unconditional literal",
    ):
        inventory.build_inventory(root)


def test_module_binding_analysis_handles_annotations_walrus_and_delete(tmp_path):
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(
            "\nGHOST: int\nif (WALRUS := 4):\n    pass\n"
            "[None for _ in range(1) if (COMP_WALRUS := True)]\n"
        )
    bindings = inventory.build_inventory(root)["rag_source_owner"]["explicit_bindings"]
    assert "GHOST" not in bindings
    assert "WALRUS" in bindings
    assert "COMP_WALRUS" in bindings

    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write("TO_DELETE = 1\ndel TO_DELETE\n")
    with pytest.raises(
        inventory.ArchitectureInventoryError,
        match="module-scope del requires explicit namespace analysis",
    ):
        inventory.build_inventory(root)


def test_dynamic_import_edges_track_alias_flow_context_and_provenance():
    tree = ast.parse(
        """import importlib as il
from importlib import import_module as load
from typing import TYPE_CHECKING as TC
import typing as t

il.import_module("pkg")
load(".child", __package__)

class Eager:
    plugin = load("eager")

@load("decorator").decorate
def work(value=load("defaults")):
    load("conditional")

if TC:
    load("typed_direct")
if t.TYPE_CHECKING:
    load("typed_module")

[load("comp_body") for load in load("comp_iter")]

def local_shadow():
    load("local_before_import")
    from importlib import import_module as load

object().import_module("ignored")
il = object()
il.import_module("ignored")
load = object()
load("ignored")
"""
    )
    known = {
        "comp_body", "comp_iter", "conditional", "decorator", "defaults",
        "eager", "ignored", "local_before_import", "package.child",
        "package.module", "pkg", "typed_direct", "typed_module",
    }

    edges = inventory._module_import_edges(
        "package.module", Path("package/module.py"), tree, known
    )

    assert edges == {
        "conditional": {("conditional", "dynamic")},
        "comp_iter": {("runtime", "dynamic")},
        "decorator": {("runtime", "dynamic")},
        "defaults": {("runtime", "dynamic")},
        "eager": {("runtime", "dynamic")},
        "package.child": {("runtime", "dynamic")},
        "pkg": {("runtime", "dynamic")},
        "typed_direct": {("type_only", "dynamic")},
        "typed_module": {("type_only", "dynamic")},
    }


def test_try_node_abstraction_is_cross_version():
    tree = ast.parse(
        "try:\n    PRIMARY = 1\nexcept Exception:\n    FALLBACK = 2\n"
    )

    assert isinstance(tree.body[0], inventory._TryNode)
    assert {"PRIMARY", "FALLBACK"} <= set(
        inventory._module_binding_origins(tree)
    )
    if not hasattr(ast, "TryStar"):
        assert inventory._TryNode is ast.Try


@pytest.mark.skipif(
    not hasattr(ast, "TryStar"), reason="except* syntax requires Python 3.11+"
)
def test_try_star_branches_retain_all_architecture_analysis():
    tree = ast.parse(
        """import importlib
import rag as facade

try:
    BODY = importlib.import_module("try_body")
    body_value = facade.PUBLIC
except* Exception:
    HANDLER = importlib.import_module("try_handler")
    handler_value = facade._PRIVATE
else:
    ELSE = importlib.import_module("try_else")
    else_value = facade.PUBLIC
finally:
    FINAL = importlib.import_module("try_final")
    final_value = facade.PUBLIC
"""
    )
    known = {"try_body", "try_else", "try_final", "try_handler"}

    edges = inventory._module_import_edges(
        "consumer", Path("consumer.py"), tree, known
    )
    assert edges == {
        name: {("conditional", "dynamic")} for name in known
    }

    origins = inventory._module_binding_origins(tree)
    assert {"BODY", "ELSE", "FINAL", "HANDLER"} <= set(origins)

    resolver = inventory._FacadeAnalysis(tree, {})
    accesses = inventory._facade_attribute_accesses(
        tree, resolver, "consumer.py"
    )
    assert {(item["line"], item["path"]) for item in accesses} == {
        (6, "PUBLIC"),
        (9, "_PRIVATE"),
        (12, "PUBLIC"),
        (15, "PUBLIC"),
    }


def test_compact_import_edges_preserve_context_origin_correlation():
    static_runtime_tree = ast.parse(
        "import importlib\nimport pkg\ndef f():\n importlib.import_module('pkg')\n"
    )
    dynamic_runtime_tree = ast.parse(
        "import importlib\nimportlib.import_module('pkg')\ndef f():\n import pkg\n"
    )
    known = {"consumer", "pkg"}
    static_runtime = inventory._compact_import_edges(
        inventory._module_import_edges(
            "consumer", Path("consumer.py"), static_runtime_tree, known
        )
    )
    dynamic_runtime = inventory._compact_import_edges(
        inventory._module_import_edges(
            "consumer", Path("consumer.py"), dynamic_runtime_tree, known
        )
    )

    assert static_runtime != dynamic_runtime
    assert static_runtime == [{
        "evidence": [
            {"context": "conditional", "origin": "dynamic"},
            {"context": "runtime", "origin": "static"},
        ],
        "target": "pkg",
    }]


def test_facade_aliases_are_flow_aware_and_comprehension_scoped():
    path = Path("tests/test_alias_flow.py")
    tree = ast.parse(
        """import rag as facade
from rag import operating as op
before = op.getenv
op = object()
after = op.getenv
op = facade.operating
restored = op.getenv
values = [op.getenv for op in op.environ]
def shadow(op):
    return op.getenv
op.getenv = replacement
del op.path
monkeypatch.setattr(op, "getenv", replacement)
class Container:
    facade = object()
    def method(self):
        return facade._GLOBAL
"""
    )
    analysis = inventory._FacadeAnalysis(tree, {})
    accesses = inventory._facade_attribute_accesses(
        tree, analysis, path.as_posix()
    )
    observed = {(item["line"], item["path"], item["context"]) for item in accesses}

    assert (3, "operating.getenv", "load") in observed
    assert (5, "operating.getenv", "load") not in observed
    assert (7, "operating.getenv", "load") in observed
    assert (8, "operating.environ", "load") in observed
    assert (8, "operating.getenv", "load") not in observed
    assert (10, "operating.getenv", "load") not in observed
    assert (11, "operating.getenv", "store") in observed
    assert (12, "operating.path", "del") in observed
    assert (17, "_GLOBAL", "load") in observed

    patches = inventory._patch_inventory({path: tree}, {})
    assert [item["target"] for item in patches["nested"]] == [
        "operating.getenv"
    ]
    mutations = inventory._direct_mutation_inventory({path: tree}, {})
    assert [item["target"] for item in mutations["nested"]] == [
        "operating.getenv", "operating.path",
    ]


def test_pep_695_type_alias_is_a_module_binding_when_supported(tmp_path):
    if not hasattr(ast, "TypeAlias"):
        pytest.skip("PEP 695 syntax requires Python 3.12+")
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write("\ntype PublicAlias = int\n")

    bindings = inventory.build_inventory(root)["rag_source_owner"]["explicit_bindings"]

    assert "PublicAlias" in bindings


def test_runtime_probe_initializes_sysconfig_without_permitting_tilde_paths(
    tmp_path,
):
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(
            """
import os as probe_os
import sysconfig as probe_sysconfig

_PROBE_USER_BASE = probe_sysconfig.get_config_var("userbase")
if probe_os.path.normcase(_PROBE_USER_BASE) != probe_os.path.normcase(
    probe_os.environ["PYTHONUSERBASE"]
):
    raise RuntimeError("sysconfig did not use the isolated Python user base")
try:
    probe_os.path.expanduser("~/.operator-material")
except RuntimeError:
    _PROBE_TILDE_DENIED = True
else:
    raise RuntimeError("application tilde expansion was not disabled")
"""
        )

    contract = inventory._runtime_facade_contract(root)

    assert {"_PROBE_TILDE_DENIED", "_PROBE_USER_BASE"} <= set(
        contract["vars_names"]
    )


def test_runtime_probe_scrubs_and_redirects_ambient_environment(
    tmp_path, monkeypatch,
):
    root = _example_repository(tmp_path)
    secrets = {
        "HOME": "C:/sensitive/home",
        "USERPROFILE": "C:/sensitive/profile",
        "HOMEDRIVE": "Z:",
        "HOMEPATH": "/sensitive",
        "LANG": "private-locale",
        "LC_ALL": "private-locale",
        "PYTHONUSERBASE": "C:/sensitive/python-user-base",
        "RAG_LLM_CACHE_DIR": "C:/sensitive/cache",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    observed: dict[str, str] = {}
    original_run = inventory.process_supervision._run_cli_with_deadline

    def recording_run(*args, **kwargs):
        observed.update(kwargs["environment_overrides"])
        return original_run(*args, **kwargs)

    monkeypatch.setattr(
        inventory.process_supervision, "_run_cli_with_deadline", recording_run
    )
    inventory._runtime_facade_contract(root)

    assert observed["HOME"] is None
    assert not set(secrets.values()) & set(observed.values())
    for name in (
        "APPDATA", "LOCALAPPDATA", "RAG_LLM_CACHE_DIR",
        "RAG_MODEL_ARTIFACT_CACHE", "RAG_PIPELINE_OUTPUT_ROOT", "TEMP",
        "TMP", "PYTHONUSERBASE", "USERPROFILE", "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
    ):
        assert name in observed


def test_runtime_probe_failure_is_redacted(tmp_path, monkeypatch):
    root = _example_repository(tmp_path)

    monkeypatch.setattr(
        inventory.process_supervision,
        "_run_cli_with_deadline",
        lambda *_args, **_kwargs: 1,
    )
    with pytest.raises(inventory.ArchitectureInventoryError) as caught:
        inventory._runtime_facade_contract(root)
    assert str(caught.value) == "Isolated rag runtime probe failed (nonzero exit)"


def test_runtime_probe_enforces_output_ceiling_during_supervision(
    tmp_path, monkeypatch,
):
    root = _example_repository(tmp_path)

    def overflow(*_args, **kwargs):
        kwargs["stdout_target"].write(b"x" * (4 * 1024 * 1024 + 1))
        kwargs["stdout_target"].flush()
        kwargs["heartbeat"](None)
        return 0

    monkeypatch.setattr(
        inventory.process_supervision, "_run_cli_with_deadline", overflow
    )

    with pytest.raises(
        inventory.ArchitectureInventoryError, match="exceeded its output ceiling"
    ):
        inventory._runtime_facade_contract(root)


def test_runtime_path_defaults_are_separator_neutral(tmp_path):
    root = _example_repository(tmp_path)
    rag_path = root / "rag.py"
    source = rag_path.read_text(encoding="utf-8")
    source += (
        "\nfrom pathlib import Path\n"
        "def path_default(value=Path(r'folder\\child')):\n"
        "    return value\n"
    )
    _write(rag_path, source)
    backslash_contract = inventory._runtime_facade_contract(root)
    backslash = next(
        item for item in backslash_contract["callables"]
        if item["name"] == "path_default"
    )

    _write(rag_path, source.replace("r'folder\\\\child'", "'folder/child'"))
    slash_contract = inventory._runtime_facade_contract(root)
    slash = next(
        item for item in slash_contract["callables"]
        if item["name"] == "path_default"
    )

    assert backslash == slash


def test_runtime_contract_preserves_generic_type_arguments(tmp_path):
    root = _example_repository(tmp_path)
    rag_path = root / "rag.py"
    source = rag_path.read_text(encoding="utf-8").replace(
        "a: int", "a: list[int]",
    )
    _write(rag_path, source)
    int_contract = inventory._runtime_facade_contract(root)
    int_local = next(
        item for item in int_contract["callables"] if item["name"] == "local"
    )

    _write(rag_path, source.replace("a: list[int]", "a: list[str]"))
    str_contract = inventory._runtime_facade_contract(root)
    str_local = next(
        item for item in str_contract["callables"] if item["name"] == "local"
    )

    assert int_local["runtime_signature_sha256"] != str_local[
        "runtime_signature_sha256"
    ]
    assert int_local["type_hints_sha256"] != str_local["type_hints_sha256"]


def test_runtime_contract_normalizes_only_the_public_path_identity(tmp_path):
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(
            """
from pathlib import Path as PublicPath

# Exercise CPython 3.13's private implementation identity on every supported
# interpreter without changing the architecture probe process itself.
PublicPath.__module__ = "pathlib._local"

class ProjectPath:
    pass

class UnrelatedPath:
    pass

UnrelatedPath.__module__ = "pathlib._local"
UnrelatedPath.__qualname__ = "Path"

def public_path_annotation(
    value: PublicPath,
) -> tuple[PublicPath, list[PublicPath]]:
    return value, [value]

def project_path_annotation(
    value: ProjectPath,
) -> tuple[ProjectPath, list[ProjectPath]]:
    return value, [value]

def unrelated_path_annotation(
    value: UnrelatedPath,
) -> tuple[UnrelatedPath, list[UnrelatedPath]]:
    return value, [value]
"""
        )

    contract = inventory._runtime_facade_contract(root)
    records = {item["name"]: item for item in contract["callables"]}

    assert (records["PublicPath"]["module"], records["PublicPath"]["qualname"]) == (
        "pathlib", "Path",
    )
    assert (
        records["ProjectPath"]["module"], records["ProjectPath"]["qualname"]
    ) == ("rag", "ProjectPath")
    assert (
        records["UnrelatedPath"]["module"],
        records["UnrelatedPath"]["qualname"],
    ) == ("pathlib._local", "Path")

    annotation_records = [
        records["public_path_annotation"],
        records["project_path_annotation"],
        records["unrelated_path_annotation"],
    ]
    assert len({item["runtime_signature_sha256"] for item in annotation_records}) == 3
    assert len({item["type_hints_sha256"] for item in annotation_records}) == 3


def test_runtime_contract_preserves_declared_optional_semantics(tmp_path):
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(
            """
from typing import Optional

def declared_int(value: int) -> int:
    return value

def default_none_int(value: int = None) -> int:
    return value

def explicit_optional_int(value: Optional[int] = None) -> int:
    return value
"""
        )

    contract = inventory._runtime_facade_contract(root)
    records = {item["name"]: item for item in contract["callables"]}
    declared = records["declared_int"]
    default_none = records["default_none_int"]
    explicit_optional = records["explicit_optional_int"]

    assert declared["type_hints_sha256"] == default_none["type_hints_sha256"]
    assert declared["type_hints_sha256"] != explicit_optional["type_hints_sha256"]
    assert (
        declared["runtime_signature_sha256"]
        != default_none["runtime_signature_sha256"]
    )


def test_runtime_contract_preserves_inherited_class_annotations(tmp_path):
    root = _example_repository(tmp_path)
    rag_path = root / "rag.py"
    source = rag_path.read_text(encoding="utf-8") + """

class PublicAnnotationBase:
    inherited_value: int

class PublicAnnotationChild(PublicAnnotationBase):
    pass
"""
    _write(rag_path, source)
    int_contract = inventory._runtime_facade_contract(root)
    int_child = next(
        item for item in int_contract["callables"]
        if item["name"] == "PublicAnnotationChild"
    )

    _write(rag_path, source.replace("inherited_value: int", "inherited_value: bytes"))
    bytes_contract = inventory._runtime_facade_contract(root)
    bytes_child = next(
        item for item in bytes_contract["callables"]
        if item["name"] == "PublicAnnotationChild"
    )

    assert int_child["type_hints_status"] == "ok"
    assert bytes_child["type_hints_status"] == "ok"
    assert int_child["type_hints_sha256"] != bytes_child["type_hints_sha256"]


def test_runtime_contract_resolves_nested_function_forward_references(tmp_path):
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(
            """
class ForwardTarget:
    pass

def nested_forward(value: list["ForwardTarget"]) -> list["ForwardTarget"]:
    return value

def nested_resolved(value: list[ForwardTarget]) -> list[ForwardTarget]:
    return value
"""
        )

    contract = inventory._runtime_facade_contract(root)
    records = {item["name"]: item for item in contract["callables"]}

    assert (
        records["nested_forward"]["type_hints_sha256"]
        == records["nested_resolved"]["type_hints_sha256"]
    )


def test_runtime_contract_resolves_nested_class_forward_references(tmp_path):
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(
            """
class NestedClassTarget:
    pass

class NestedForwardClass:
    TargetAlias = NestedClassTarget
    items: list["TargetAlias"]

class NestedResolvedClass:
    items: list[NestedClassTarget]
"""
        )

    contract = inventory._runtime_facade_contract(root)
    records = {item["name"]: item for item in contract["callables"]}

    assert (
        records["NestedForwardClass"]["type_hints_sha256"]
        == records["NestedResolvedClass"]["type_hints_sha256"]
    )


def test_runtime_contract_resolves_inherited_nested_refs_with_owner_namespace(
    tmp_path,
):
    root = _example_repository(tmp_path)
    _write(
        root / "hidden.py",
        """class HiddenTarget:
    pass

class HiddenBase:
    TargetAlias = HiddenTarget
    base_items: list["TargetAlias"]
    overridden_items: list["TargetAlias"]
""",
    )
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(
            """
from hidden import HiddenBase, HiddenTarget

class PublicTarget:
    pass

class PublicChild(HiddenBase):
    TargetAlias = PublicTarget
    overridden_items: list["TargetAlias"]

class PublicResolved:
    base_items: list[HiddenTarget]
    overridden_items: list[PublicTarget]
"""
        )

    contract = inventory._runtime_facade_contract(root)
    records = {item["name"]: item for item in contract["callables"]}

    assert (
        records["PublicChild"]["type_hints_sha256"]
        == records["PublicResolved"]["type_hints_sha256"]
    )


def test_runtime_contract_stabilizes_only_typing_any_classification(tmp_path):
    root = _example_repository(tmp_path)
    with (root / "rag.py").open("a", encoding="utf-8") as handle:
        handle.write(
            """
from typing import Any as PublicAny

class OrdinaryClass:
    pass

def ordinary_function():
    pass

class CallableObject:
    def __call__(self):
        pass

unrelated_callable = CallableObject()
"""
        )

    contract = inventory._runtime_facade_contract(root)
    records = {item["name"]: item for item in contract["callables"]}

    assert {
        key: records["PublicAny"][key]
        for key in ("kind", "module", "qualname")
    } == {"kind": "class", "module": "typing", "qualname": "Any"}
    assert records["OrdinaryClass"]["kind"] == "class"
    assert records["ordinary_function"]["kind"] == "function"
    assert "unrelated_callable" not in records


@pytest.mark.parametrize(
    "payload, message",
    [
        ('{"schema_version": 3, "schema_version": 3}', "duplicate JSON field"),
        ('{"schema_version": NaN}', "non-standard JSON number"),
        ('{"schema_version": 3}', "inventory fields differ"),
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
    # Preserve roughly the review-growth headroom established when schema v3
    # first compacted the canonical inventory to about one megabyte.
    assert baseline.stat().st_size <= 1_600_000
    assert value["source_architecture"]["import_graph"]["cyclic_components"] == []
    assert [
        item["module"] for item in value["consumers"]["production"]
    ] == ["eval", "service_search_worker", "ui"]


def test_repository_rag_static_evidence_and_runtime_contract_are_separate():
    value = inventory.build_inventory(PROJECT_ROOT)
    variants = set(value["rag_runtime_contract"]["interpreter_variant_exclusions"])
    runtime_names = sorted(
        name for name in vars(rag) if name not in variants
    )
    namespace: dict[str, object] = {}
    exec("from rag import *", namespace)
    import_star_names = sorted(name for name in namespace if name != "__builtins__")

    assert value["rag_runtime_contract"]["vars_names"] == runtime_names
    assert value["rag_runtime_contract"]["import_star_names"] == import_star_names
    assert value["rag_runtime_contract"]["platform_variant_bindings"] == [{
        "name": "getpass",
        "token": "python-stdlib:getpass:platform-dispatch:v1",
    }]
    assert all(
        set(item) == {"contract_sha256", "kind", "module", "name", "qualname"}
        for item in value["rag_runtime_contract"]["callables"]
    )
    assert set(value["rag_source_owner"]["explicit_bindings"]) <= set(runtime_names)
    assert all(value["rag_runtime_contract"]["behavior"].values())

    seams = value["consumers"]["tests"]["monkeypatch_seams"]
    ui_indirect_count = sum(
        source["count"]
        for item in [*seams["top_level"], *seams["nested"]]
        for source in item["source_counts"]
        if source["path"] == "tests/test_ui.py"
    )
    assert ui_indirect_count == 6

    mutations = value["consumers"]["tests"]["direct_mutations"]
    mutation_sources = {
        item["target"]: {
            source["path"]: source["count"]
            for source in item["source_counts"]
        }
        for item in [*mutations["top_level"], *mutations["nested"]]
    }
    assert mutation_sources["_llm_runtime"]["tests/test_llm_runtime.py"] == 2
    assert mutation_sources["_embed_texts"][
        "tests/test_vector_store_lock_release.py"
    ] == 2
    assert mutation_sources["_validate_embedding_token_counts"][
        "tests/test_vector_store_lock_release.py"
    ] == 2
