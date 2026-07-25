from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

from tools.check_python_sources import tracked_python_paths


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _application_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for relative in tracked_python_paths(PROJECT_ROOT):
        if relative.parts[0] == "tests":
            continue
        if relative.name == "__init__.py":
            module = ".".join(relative.parent.parts)
        else:
            module = ".".join((*relative.parent.parts, relative.stem))
        assert module and module not in modules, module
        modules[module] = PROJECT_ROOT / relative
    return modules


def _known_prefixes(name: str, known: set[str]) -> set[str]:
    parts = name.split(".")
    return {
        ".".join(parts[:index])
        for index in range(1, len(parts) + 1)
        if ".".join(parts[:index]) in known
    }


def _absolute_from_import(
        current: str, path: Path, node: ast.ImportFrom,
) -> str:
    if node.level == 0:
        return node.module or ""
    package = (
        current.split(".")
        if path.name == "__init__.py"
        else current.split(".")[:-1]
    )
    ascend = node.level - 1
    if ascend > len(package):
        return ""
    prefix = package[:len(package) - ascend]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _import_targets(
        current: str, path: Path, node: ast.Import | ast.ImportFrom,
        known: set[str],
) -> set[str]:
    targets: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            targets.update(_known_prefixes(alias.name, known))
        return targets

    base = _absolute_from_import(current, path, node)
    targets.update(_known_prefixes(base, known))
    if node.level and not base:
        return targets
    for alias in node.names:
        if alias.name == "*":
            continue
        candidate = f"{base}.{alias.name}" if base else alias.name
        targets.update(_known_prefixes(candidate, known))
    return targets


def _first_party_import_graph() -> dict[str, set[str]]:
    modules = _application_modules()
    known = set(modules)
    graph = {module: set() for module in modules}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                graph[module].update(
                    _import_targets(module, path, node, known))
    return graph


def _cyclic_components(graph: dict[str, set[str]]) -> set[frozenset[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: set[frozenset[str]] = set()

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for dependency in sorted(graph[node]):
            if dependency not in indices:
                visit(dependency)
                lowlinks[node] = min(lowlinks[node], lowlinks[dependency])
            elif dependency in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[dependency])
        if lowlinks[node] != indices[node]:
            return
        component = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1 or node in graph[node]:
            result.add(frozenset(component))

    for module in sorted(graph):
        if module not in indices:
            visit(module)
    return result


def _transitive_dependencies(
        graph: dict[str, set[str]], module: str,
) -> set[str]:
    result: set[str] = set()
    pending = list(graph[module])
    while pending:
        dependency = pending.pop()
        if dependency in result:
            continue
        result.add(dependency)
        pending.extend(graph[dependency])
    result.discard(module)
    return result


def _raw_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module.split(".", 1)[0])
    imports.discard("__future__")
    return imports


def _run_isolated(source: str) -> subprocess.CompletedProcess[str]:
    program = (
        "import sys; "
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
        f"{source}"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", program],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )


def test_first_party_import_graph_is_acyclic():
    graph = _first_party_import_graph()

    assert _cyclic_components(graph) == set()


def test_job_manager_depends_on_runtime_binding_without_loading_rag():
    graph = _first_party_import_graph()

    assert "runtime_supervision" in graph["job_manager"]
    assert "rag" not in graph["job_manager"]
    assert "rag" not in _transitive_dependencies(graph, "job_manager")
    assert graph["runtime_supervision"] == {
        "cli_policy", "process_supervision", "run_telemetry",
    }
    assert "runtime_supervision" in graph["rag"]
    assert "rag" not in _transitive_dependencies(
        graph, "runtime_supervision")
    runtime_path = _application_modules()["runtime_supervision"]
    assert _raw_import_roots(runtime_path) == {
        "cli_policy", "collections", "dataclasses", "math", "pathlib",
        "process_supervision", "run_telemetry", "sys", "threading",
        "types", "typing",
    }


def test_job_manager_and_rag_import_cleanly_in_both_orders():
    for imports in (
        "import job_manager; assert 'rag' not in sys.modules; import rag",
        "import rag; assert 'job_manager' not in sys.modules; "
        "import job_manager",
    ):
        result = _run_isolated(imports)
        assert result.returncode == 0, result.stderr


def test_service_runtime_uses_inward_binding_without_loading_rag():
    graph = _first_party_import_graph()

    # ``job_manager`` is intentionally still a host dependency. Removing that
    # remaining composition edge belongs to a later R8 milestone.
    assert graph["service_runtime"] == {
        "job_manager",
        "job_runtime",
        "release_security",
        "retention",
        "service_contracts",
        "service_runtime_binding",
        "storage_policy",
    }
    assert "rag" not in _transitive_dependencies(graph, "service_runtime")
    assert graph["service_runtime_binding"] == {
        "embedding_policy",
        "resource_lease",
        "runtime_supervision",
    }
    assert "rag" not in _transitive_dependencies(
        graph, "service_runtime_binding")

    result = _run_isolated(
        "import service_runtime_binding as binding; "
        "assert binding.ServiceRuntimeBinding.__dataclass_params__.frozen"
    )
    assert result.returncode == 0, result.stderr


def test_service_search_worker_is_the_only_rag_service_composition_shell():
    graph = _first_party_import_graph()

    composition_shells = {
        module for module, dependencies in graph.items()
        if {"rag", "service_runtime"} <= dependencies
    }
    assert composition_shells == {"service_search_worker"}
    assert graph["service_search_worker"] == {"rag", "service_runtime"}
    for host in ("rag", "service_runtime"):
        assert "service_search_worker" not in graph[host]
        assert "service_search_worker" not in _transitive_dependencies(
            graph, host)


def test_service_runtime_boundary_raw_imports_are_pinned():
    modules = _application_modules()

    assert _raw_import_roots(modules["service_runtime"]) == {
        "dataclasses", "hashlib", "job_manager", "job_runtime", "json",
        "os", "pathlib", "release_security", "retention",
        "service_contracts", "service_runtime_binding", "stat",
        "storage_policy", "tempfile", "threading", "typing",
    }
    assert _raw_import_roots(modules["service_runtime_binding"]) == {
        "collections", "dataclasses", "embedding_policy", "pathlib",
        "resource_lease", "runtime_supervision", "typing",
    }
    assert _raw_import_roots(modules["service_search_worker"]) == {
        "collections", "logging", "os", "pathlib", "rag",
        "service_runtime", "sys",
    }
    assert _raw_import_roots(modules["embedding_policy"]) == set()
    assert _raw_import_roots(modules["resource_lease"]) == {
        "collections", "dataclasses", "errno", "fcntl", "hashlib",
        "logging", "math", "msvcrt", "os", "pathlib", "re",
        "retention", "stat", "storage_policy", "threading", "time",
        "typing",
    }


def test_service_runtime_isolated_import_avoids_rag_and_heavy_backends():
    result = _run_isolated(
        "import service_runtime; "
        "forbidden = ('rag', 'qdrant', 'qdrant_client', 'chromadb', 'torch', "
        "'transformers', 'docling'); "
        "loaded = tuple(sys.modules); "
        "assert not {root: [name for name in loaded if name == root or "
        "name.startswith(root + '.')] for root in forbidden "
        "if any(name == root or name.startswith(root + '.') "
        "for name in loaded)}"
    )
    assert result.returncode == 0, result.stderr


def test_service_runtime_and_rag_import_cleanly_in_both_orders():
    for imports in (
        "import service_runtime; assert 'rag' not in sys.modules; import rag",
        "import rag; assert 'service_runtime' not in sys.modules; "
        "import service_runtime",
    ):
        result = _run_isolated(imports)
        assert result.returncode == 0, result.stderr


def test_import_resolver_tracks_package_initializers_and_known_prefixes():
    known = {
        "pkg", "pkg.child", "pkg.child.deep", "pkg.child.module",
        "pkg.sibling",
    }
    absolute = ast.parse("import pkg.child.deep").body[0]
    package_relative = ast.parse("from . import child").body[0]
    parent_relative = ast.parse("from .. import sibling").body[0]
    assert isinstance(absolute, ast.Import)
    assert isinstance(package_relative, ast.ImportFrom)
    assert isinstance(parent_relative, ast.ImportFrom)

    assert _import_targets(
        "consumer", Path("consumer.py"), absolute, known,
    ) == {"pkg", "pkg.child", "pkg.child.deep"}
    assert _import_targets(
        "pkg", Path("pkg/__init__.py"), package_relative, known,
    ) == {"pkg", "pkg.child"}
    assert _import_targets(
        "pkg.child.module", Path("pkg/child/module.py"),
        parent_relative, known,
    ) == {"pkg", "pkg.sibling"}


def test_release_input_contract_has_one_way_dependency_direction():
    graph = _first_party_import_graph()

    assert "evaluation_inputs" in graph
    assert graph["evaluation_inputs"] == {"artifact_io", "storage_policy"}
    assert _transitive_dependencies(graph, "evaluation_inputs") == {
        "artifact_io", "storage_policy"}
    assert graph["evaluation_release"] == {
        "evaluation_contract",
        "evaluation_inputs",
        "model_artifacts",
        "retrieval_core",
    }
    assert "evaluation_inputs" in graph["evaluation_review"]
    assert all(
        "evaluation_release" not in component
        for component in _cyclic_components(graph)
    )
    input_path = _application_modules()["evaluation_inputs"]
    assert _raw_import_roots(input_path) == {
        "artifact_io", "json", "math", "pathlib", "re", "storage_policy",
    }


def test_evaluation_query_domain_dissolves_the_application_cycle():
    graph = _first_party_import_graph()

    assert graph["evaluation_queries"] == {
        "evaluation_contract", "retrieval_core", "table_retrieval_core",
    }
    assert _transitive_dependencies(graph, "evaluation_queries") == {
        "evaluation_contract", "retrieval_core", "table_retrieval_core",
    }
    assert "evaluation_queries" in graph["eval"]
    assert "evaluation_queries" in graph["evaluation_review"]
    assert "eval" not in graph["evaluation_review"]
    assert "eval" not in _transitive_dependencies(
        graph, "evaluation_review")
    query_path = _application_modules()["evaluation_queries"]
    assert _raw_import_roots(query_path) == {
        "evaluation_contract", "math", "re", "retrieval_core",
        "table_retrieval_core",
    }
