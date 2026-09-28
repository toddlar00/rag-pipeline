from __future__ import annotations

import ast
import importlib.util
from collections.abc import Mapping, Set
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

from tools.check_python_sources import tracked_python_paths


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRES_SERVICE_HTTP = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None,
    reason="service HTTP dependencies are not installed",
)


@pytest.fixture(scope="module", name="application_modules")
def _application_modules() -> Mapping[str, Path]:
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
    return MappingProxyType(modules)


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


@pytest.fixture(scope="module", name="first_party_import_graph")
def _first_party_import_graph(
        application_modules: Mapping[str, Path],
) -> Mapping[str, frozenset[str]]:
    modules = application_modules
    known = set(modules)
    graph = {module: set() for module in modules}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                graph[module].update(
                    _import_targets(module, path, node, known))
    return MappingProxyType({module: frozenset(edges) for module, edges in graph.items()})


def _cyclic_components(graph: Mapping[str, Set[str]]) -> set[frozenset[str]]:
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
        graph: Mapping[str, Set[str]], module: str,
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
        [sys.executable, "-I", "-B", "-c", program],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )


def test_first_party_import_graph_is_acyclic(first_party_import_graph):
    graph = first_party_import_graph

    assert _cyclic_components(graph) == set()


def test_inventory_static_edges_exactly_match_independent_ast_graph(
        first_party_import_graph, repository_inventory):
    expected = first_party_import_graph
    value = repository_inventory
    observed = {
        module["module"]: {
            edge["target"]
            for edge in module["import_edges"]
            if any(item["origin"] == "static" for item in edge["evidence"])
        }
        for module in value["source_architecture"]["modules"]
    }

    assert observed == expected


def test_job_coordination_depends_inward_and_manager_is_only_a_facade(application_modules, first_party_import_graph):
    graph = first_party_import_graph

    assert graph["job_application"] == {
        "job_coordination", "job_coordination_contracts", "job_runtime"}
    assert graph["job_manager"] == {
        "job_coordination", "job_coordination_contracts"}
    assert {
        module for module, dependencies in graph.items()
        if "job_manager" in dependencies
    } == set()
    for consumer in ("job_application", "rag", "service_runtime", "ui"):
        assert "job_manager" not in _transitive_dependencies(graph, consumer)
    assert "runtime_supervision" in graph["job_coordination"]
    assert "rag" not in graph["job_manager"]
    assert "rag" not in _transitive_dependencies(graph, "job_manager")
    assert graph["job_coordination_contracts"] == set()
    assert not ({"rag", "service_api", "service_runtime", "ui"}
                & _transitive_dependencies(graph, "job_application"))
    assert graph["runtime_supervision"] == {
        "cli_policy", "process_supervision", "run_telemetry",
    }
    assert "runtime_supervision" in graph["rag"]
    assert "rag" not in _transitive_dependencies(
        graph, "runtime_supervision")
    runtime_path = application_modules["runtime_supervision"]
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


def test_job_application_import_is_inward_and_facade_order_independent():
    for imports in (
        "import job_application; "
        "assert 'job_manager' not in sys.modules; "
        "assert 'rag' not in sys.modules; import job_manager; import rag",
        "import rag; assert 'job_manager' not in sys.modules; "
        "import job_application; import job_manager",
    ):
        result = _run_isolated(imports)
        assert result.returncode == 0, result.stderr


def test_service_runtime_uses_inward_binding_without_loading_rag(first_party_import_graph):
    graph = first_party_import_graph

    assert graph["service_runtime"] == {
        "job_coordination",
        "job_coordination_contracts",
        "job_runtime",
        "release_security",
        "retention",
        "service_contracts",
        "service_evidence_contracts",
        "service_runtime_binding",
        "storage_policy",
    }
    assert "job_manager" not in _transitive_dependencies(
        graph, "service_runtime")
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


def test_service_http_and_outer_root_have_exact_one_way_dependencies(first_party_import_graph):
    graph = first_party_import_graph
    inbound = {
        dependency: {
            module for module, dependencies in graph.items()
            if dependency in dependencies
        }
        for dependency in graph
    }

    assert graph["service_http"] == {"service_contracts"}
    assert graph["application_composition"] == {
        "job_coordination",
        "service_evidence_http",
        "service_evidence_runtime",
        "service_http",
        "service_runtime",
        "service_runtime_binding",
    }
    assert graph["service_api"] == {
        "application_composition",
        "release_security",
        "service_contracts",
        "service_http",
        "service_runtime",
        "storage_policy",
    }
    assert inbound["service_api"] == set()
    assert inbound["application_composition"] == {"service_api"}
    assert inbound["service_http"] == {
        "application_composition", "service_api"}
    assert _transitive_dependencies(graph, "service_http") == {
        "service_contracts"}
    assert not ({"service_api", "rag", "job_manager"}
                & _transitive_dependencies(
                    graph, "application_composition"))


def test_service_http_root_and_facade_raw_imports_are_pinned(application_modules):
    modules = application_modules

    assert _raw_import_roots(modules["service_http"]) == {
        "asyncio", "concurrent", "contextlib", "contextvars", "dataclasses", "fastapi", "hmac",
        "ipaddress", "re", "service_contracts", "starlette",
        "threading", "typing", "uuid",
    }
    assert _raw_import_roots(modules["application_composition"]) == {
        "collections", "dataclasses", "job_coordination",
        "service_evidence_http", "service_evidence_runtime",
        "service_http", "service_runtime", "service_runtime_binding",
        "threading", "typing",
    }
    assert _raw_import_roots(modules["service_api"]) == {
        "application_composition", "argparse", "ipaddress", "os",
        "pathlib", "release_security", "secrets", "service_contracts",
        "service_http", "service_runtime", "stat", "storage_policy",
        "typing", "uvicorn",
    }


def test_service_search_worker_is_the_only_rag_service_composition_shell(first_party_import_graph):
    graph = first_party_import_graph

    composition_shells = {
        module for module, dependencies in graph.items()
        if {"rag", "service_runtime"} <= dependencies
    }
    assert composition_shells == {"service_search_worker"}
    assert graph["service_search_worker"] == {
        "rag", "service_runtime", "service_evidence_runtime", "service_evidence_search"}
    for host in ("rag", "service_runtime"):
        assert "service_search_worker" not in graph[host]
        assert "service_search_worker" not in _transitive_dependencies(
            graph, host)


def test_opt_in_evidence_uses_one_way_host_and_worker_boundaries(first_party_import_graph):
    graph = first_party_import_graph
    assert graph["service_evidence_contracts"] == {"service_contracts"}
    assert graph["service_evidence_http"] == {"service_contracts", "service_evidence_contracts"}
    assert graph["service_evidence_runtime"] == {
        "release_security", "service_contracts", "service_evidence_contracts",
        "service_runtime", "service_runtime_binding", "storage_policy"}
    assert graph["service_evidence_search"] == {
        "artifact_io", "document_profiles", "evaluation_inputs", "index_state",
        "ocr_recovery_comparison", "quality_core", "release_security", "service_contracts",
        "service_evidence_contracts", "source_fidelity_core", "storage_policy", "table_retrieval_core"}
    inbound = {dependency: {module for module, dependencies in graph.items() if dependency in dependencies}
               for dependency in graph}
    assert inbound["service_evidence_http"] == {"application_composition"}
    assert inbound["service_evidence_runtime"] == {"application_composition", "service_search_worker"}
    assert inbound["service_evidence_search"] == {"service_search_worker"}
    for host in ("service_runtime", "service_evidence_runtime", "application_composition"):
        assert not {"rag", "job_manager", "service_evidence_search"} & _transitive_dependencies(graph, host)


def test_shared_ocr_allocation_policy_stays_inward_of_readers_and_stage_adapter(application_modules, first_party_import_graph):
    graph = first_party_import_graph
    assert graph["ocr_engine_limits"] == set()
    assert graph["ocr_disposition_observer"] == set()
    assert graph["ocr_engine_guard"] == {"ocr_engine_limits", "ocr_disposition_observer"}
    assert _transitive_dependencies(graph, "ocr_engine_guard") == {
        "ocr_engine_limits", "ocr_disposition_observer"}
    for reader in ("ocr_recovery_runtime", "ocr_region_runtime", "ocr_hardscan_runtime", "ocr_stage_runtime"):
        assert "ocr_engine_limits" in graph[reader]
    assert "ocr_engine_guard" in graph["ocr_recovery_runtime"]
    assert "ocr_stage_runtime" not in _transitive_dependencies(graph, "ocr_recovery_runtime")
    modules = application_modules
    assert _raw_import_roots(modules["ocr_engine_limits"]) <= {
        "__future__", "collections", "dataclasses", "math", "numbers"}


def test_service_runtime_boundary_raw_imports_are_pinned(application_modules):
    modules = application_modules

    assert _raw_import_roots(modules["service_runtime"]) == {
        "dataclasses", "hashlib", "job_coordination",
        "job_coordination_contracts", "job_runtime", "json", "os",
        "pathlib", "release_security", "retention",
        "service_contracts", "service_evidence_contracts", "service_runtime_binding", "stat",
        "storage_policy", "tempfile", "threading", "types", "typing",
    }
    assert _raw_import_roots(modules["job_coordination_contracts"]) == {
        "collections", "dataclasses",
    }
    assert _raw_import_roots(modules["job_application"]) == {
        "collections", "dataclasses", "job_coordination",
        "job_coordination_contracts", "job_runtime",
    }
    assert _raw_import_roots(modules["service_runtime_binding"]) == {
        "collections", "dataclasses", "embedding_policy", "pathlib",
        "resource_lease", "runtime_supervision", "typing",
    }
    assert _raw_import_roots(modules["service_search_worker"]) == {
        "collections", "logging", "os", "pathlib", "rag",
        "service_runtime", "service_evidence_runtime", "service_evidence_search", "sys",
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
        "forbidden = ('application_composition', 'service_http', "
        "'service_api', 'fastapi', 'starlette', 'job_manager', 'rag', "
        "'qdrant', 'qdrant_client', 'chromadb', 'torch', 'transformers', "
        "'docling'); "
        "loaded = tuple(sys.modules); "
        "assert not {root: [name for name in loaded if name == root or "
        "name.startswith(root + '.')] for root in forbidden "
        "if any(name == root or name.startswith(root + '.') "
        "for name in loaded)}"
    )
    assert result.returncode == 0, result.stderr


@REQUIRES_SERVICE_HTTP
def test_service_http_isolated_import_avoids_runtime_and_heavy_backends():
    result = _run_isolated(
        "import service_http; "
        "forbidden = ('application_composition', 'service_api', "
        "'service_runtime', 'job_manager', 'rag', 'qdrant', "
        "'qdrant_client', 'chromadb', 'torch', 'transformers', 'docling'); "
        "loaded = tuple(sys.modules); "
        "assert not {root: [name for name in loaded if name == root or "
        "name.startswith(root + '.')] for root in forbidden "
        "if any(name == root or name.startswith(root + '.') "
        "for name in loaded)}"
    )
    assert result.returncode == 0, result.stderr


def test_outer_root_import_is_lazy_and_dependency_light():
    lazy = _run_isolated(
        "import application_composition as root; "
        "forbidden = ('fastapi', 'starlette', 'service_http', "
        "'service_runtime', 'service_runtime_binding', 'job_coordination', "
        "'service_api', 'uvicorn', 'rag', 'job_manager'); "
        "assert not [name for name in sys.modules if any(name == item or "
        "name.startswith(item + '.') for item in forbidden)]; "
        "assert root._DEFAULT_SERVICE_APPLICATION_COMPOSITION is None"
    )
    assert lazy.returncode == 0, lazy.stderr


@REQUIRES_SERVICE_HTTP
def test_default_root_resolution_avoids_facades_and_heavy_backends():
    resolved = _run_isolated(
        "import application_composition as root; "
        "value = root.default_service_application_composition(); "
        "assert value is root.default_service_application_composition(); "
        "forbidden = ('service_api', 'uvicorn', 'rag', 'job_manager', "
        "'qdrant', 'qdrant_client', 'chromadb', 'torch', 'transformers', "
        "'docling'); loaded = tuple(sys.modules); "
        "assert not [name for name in loaded if any(name == item or "
        "name.startswith(item + '.') for item in forbidden)]"
    )
    assert resolved.returncode == 0, resolved.stderr


@REQUIRES_SERVICE_HTTP
def test_service_facade_import_is_lazy_and_aliases_survive_both_orders():
    for imports in (
        "import service_http; import service_api",
        "import service_api; import service_http",
    ):
        result = _run_isolated(
            f"{imports}; import application_composition as root; "
            "assert service_api.ServiceCredentials is "
            "service_http.ServiceCredentials; "
            "assert service_api.require_reader is service_http.require_reader; "
            "assert service_api.require_admin is service_http.require_admin; "
            "assert service_api.service_openapi_document is "
            "service_http.service_openapi_document; "
            "assert service_http.ServiceCredentials.__module__ == "
            "'service_api'; assert 'uvicorn' not in sys.modules; "
            "assert 'rag' not in sys.modules; "
            "assert 'job_manager' not in sys.modules; "
            "assert root._DEFAULT_SERVICE_APPLICATION_COMPOSITION is None"
        )
        assert result.returncode == 0, result.stderr

    pickled = _run_isolated(
        "import pickle, service_http; "
        "value = service_http.ServiceCredentials('r' * 48, 'a' * 48); "
        "restored = pickle.loads(pickle.dumps(value)); "
        "import service_api; "
        "assert type(restored) is service_api.ServiceCredentials"
    )
    assert pickled.returncode == 0, pickled.stderr


@REQUIRES_SERVICE_HTTP
def test_service_http_and_runtime_import_cleanly_in_both_orders():
    for imports in (
        "import service_http; assert 'service_runtime' not in sys.modules; "
        "import service_runtime",
        "import service_runtime; assert 'service_http' not in sys.modules; "
        "import service_http",
    ):
        result = _run_isolated(imports)
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


def test_release_input_contract_has_one_way_dependency_direction(application_modules, first_party_import_graph):
    graph = first_party_import_graph

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
    input_path = application_modules["evaluation_inputs"]
    assert _raw_import_roots(input_path) == {
        "artifact_io", "json", "math", "pathlib", "re", "storage_policy",
    }


def test_evaluation_query_domain_dissolves_the_application_cycle(application_modules, first_party_import_graph):
    graph = first_party_import_graph

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
    query_path = application_modules["evaluation_queries"]
    assert _raw_import_roots(query_path) == {
        "evaluation_contract", "math", "re", "retrieval_core",
        "table_retrieval_core",
    }


def test_guided_ocr_review_has_exact_inward_and_host_only_boundaries(first_party_import_graph):
    graph = first_party_import_graph
    expected = {
        "ocr_context_authoring": {"ocr_context_evaluation"},
        "ocr_review_context_ui": {
            "ocr_context_authoring", "ocr_context_evaluation", "ocr_review", "ocr_review_runtime",
        },
        "ocr_crop_comparison": {
            "ocr_comparison", "ocr_evaluation", "ocr_hardscan_io",
            "ocr_recovery_comparison", "ocr_regions",
        },
        "ocr_crop_review_runtime": {
            "artifact_io", "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_review_runtime", "storage_policy",
        },
        "ocr_crop_preview_worker": {
            "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_review_runtime", "storage_policy",
        },
        "ocr_crop_preview_supervision": {
            "ocr_crop_preview_worker", "ocr_crop_review_runtime", "ocr_review_runtime",
            "process_supervision", "storage_policy",
        },
        "ocr_review_crop_ui": {
            "ocr_crop_comparison", "ocr_crop_review_pack_io", "ocr_crop_review_runtime", "ocr_review_crop_preview_ui",
            "ocr_review_crop_ui_common", "ocr_review_crop_uncertainty_live_ui",
        },
        # Optional presentation depends inward on the fixed profile/bounds
        # contract; it does not add a renderer or execution composition root.
        "ocr_review_crop_preview_ui": {"ocr_crop_review_runtime"},
        "ocr_review_execution_ui": {
            "ocr_hardscan", "ocr_preprocessing", "ocr_review", "ocr_review_crop_ui",
        },
        "ocr_review_execution": {
            "evaluation_inputs", "ocr_crop_comparison", "ocr_crop_preview_supervision",
            "ocr_crop_review_pack", "ocr_crop_review_runtime",
            "ocr_crop_uncertainty_comparison", "ocr_crop_uncertainty_journal",
            "ocr_detection_disposition_io", "ocr_hardscan",
            "ocr_recovery", "ocr_recovery_comparison", "ocr_regions",
            "ocr_review_runtime", "process_supervision", "resource_lease", "storage_policy",
        },
        "ocr_disposition_archive": {
            "evaluation_inputs", "model_artifacts", "ocr_checkpoint", "ocr_detection_disposition",
            "ocr_disposition_geometry", "ocr_execution_receipt", "ocr_experiment_runtime",
            "ocr_hardscan", "ocr_hardscan_io", "ocr_recovery_comparison", "ocr_regions",
        },
        "ocr_crop_review_pack": {"evaluation_inputs", "ocr_crop_comparison", "ocr_disposition_archive"},
        "ocr_crop_review_pack_io": {
            "evaluation_inputs", "ocr_crop_review_pack", "ocr_crop_uncertainty_pack",
            "ocr_recovery", "resource_lease", "storage_policy",
        },
        "ocr_review_crop_packs": {
            "ocr_crop_comparison", "ocr_crop_preview_supervision", "ocr_crop_review_pack",
            "ocr_crop_review_pack_io", "ocr_crop_review_runtime", "ocr_review_runtime",
            "ocr_crop_uncertainty_comparison", "ocr_crop_uncertainty_journal", "ocr_crop_uncertainty_pack",
        },
        "ocr_review_crop_archive_ui": {
            "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_review_runtime", "ocr_review_crop_preview_ui",
            "ocr_review_crop_ui_common", "ocr_review_crop_uncertainty", "ocr_review_crop_uncertainty_editor",
            "ocr_review_save_feedback", "ocr_crop_advice",
        },
        "ocr_crop_raster_view": {"ocr_crop_comparison"},
        "ocr_crop_quality": {"ocr_crop_comparison", "ocr_crop_raster_view"},
        "ocr_crop_advice": {"ocr_crop_comparison", "ocr_crop_quality", "ocr_crop_raster_view"},
        "ocr_crop_uncertainty_journal": {"ocr_crop_comparison", "ocr_crop_raster_view", "ocr_evaluation"},
        "ocr_crop_uncertainty_comparison": {
            "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_uncertainty_journal",
        },
        "ocr_crop_uncertainty_pack": {
            "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_review_pack",
            "ocr_crop_uncertainty_comparison", "ocr_crop_uncertainty_journal",
        },
        "ocr_review_crop_uncertainty": {"ocr_crop_raster_view", "ocr_crop_uncertainty_journal", "ocr_evaluation"},
        "ocr_review_crop_uncertainty_editor": {"ocr_review_crop_preview_ui"},
        "ocr_review_crop_uncertainty_live_ui": {
            "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_review_runtime", "ocr_review_crop_preview_ui",
            "ocr_review_crop_ui_common", "ocr_review_crop_uncertainty", "ocr_review_crop_uncertainty_editor",
            "ocr_review_save_feedback", "ocr_crop_advice",
        },
        "ocr_review_crop_ui_common": {"ocr_crop_comparison"},
        "ocr_review_save_feedback": set(),
        "ocr_spot_audit": {"ocr_evaluation", "ocr_review"},
        "ocr_spot_audit_ui": {"ocr_review_crop_ui_common", "ocr_spot_audit"},
        "ocr_review_ui": {
            "ocr_omission", "ocr_recovery", "ocr_review", "ocr_review_context_ui", "ocr_review_crop_archive_ui",
            "ocr_review_drafts", "ocr_review_execution_ui", "ocr_review_runtime",
            "ocr_spot_audit_ui",
        },
        "tools.review_ocr": {
            "ocr_review_crop_packs", "ocr_review_crop_preview_ui", "ocr_review_execution",
            "ocr_review_runtime", "ocr_review_ui", "storage_policy",
        },
    }
    # Do not silently supplement the authoritative tracked-source graph with
    # working-tree files: qualification must explicitly admit the new cohort.
    assert set(expected) <= set(graph), f"untracked or missing guided-review modules: {sorted(set(expected) - set(graph))}"
    inbound = {
        dependency: {module for module, dependencies in graph.items() if dependency in dependencies}
        for dependency in expected
    }
    assert inbound == {
        "ocr_context_authoring": {"ocr_review_context_ui", "ocr_review_drafts", "ocr_review_runtime"},
        "ocr_review_context_ui": {"ocr_review_ui"},
        "ocr_crop_comparison": {
            "ocr_crop_preview_worker", "ocr_crop_review_pack", "ocr_crop_review_runtime",
            "ocr_review_crop_archive_ui", "ocr_review_crop_packs", "ocr_review_crop_ui", "ocr_review_execution",
            "ocr_crop_raster_view", "ocr_crop_uncertainty_comparison", "ocr_crop_uncertainty_journal",
            "ocr_crop_uncertainty_pack", "ocr_review_crop_ui_common", "ocr_review_crop_uncertainty_live_ui",
            "ocr_crop_quality", "ocr_crop_advice",
        },
        "ocr_crop_review_runtime": {
            "ocr_crop_preview_supervision", "ocr_crop_preview_worker", "ocr_review_crop_packs",
            "ocr_review_crop_archive_ui", "ocr_review_crop_preview_ui", "ocr_review_crop_ui", "ocr_review_execution",
            "ocr_review_crop_uncertainty_live_ui",
        },
        "ocr_crop_preview_worker": {"ocr_crop_preview_supervision"},
        "ocr_crop_preview_supervision": {"ocr_review_crop_packs", "ocr_review_execution"},
        "ocr_review_crop_ui": {"ocr_review_execution_ui"},
        "ocr_review_crop_preview_ui": {
            "ocr_review_crop_archive_ui", "ocr_review_crop_ui", "ocr_review_crop_uncertainty_editor",
            "ocr_review_crop_uncertainty_live_ui", "tools.review_ocr",
        },
        "ocr_review_execution": {"tools.review_ocr"},
        "ocr_review_execution_ui": {"ocr_review_ui"},
        "ocr_disposition_archive": {"ocr_crop_review_pack"},
        "ocr_crop_review_pack": {
            "ocr_crop_review_pack_io", "ocr_crop_uncertainty_pack", "ocr_review_crop_packs", "ocr_review_execution",
        },
        "ocr_crop_review_pack_io": {"ocr_review_crop_packs", "ocr_review_crop_ui"},
        "ocr_review_crop_packs": {"tools.review_ocr"},
        "ocr_review_crop_archive_ui": {"ocr_review_ui"},
        "ocr_crop_raster_view": {
            "ocr_crop_preview_worker", "ocr_crop_review_runtime", "ocr_crop_uncertainty_comparison",
            "ocr_crop_uncertainty_journal", "ocr_crop_uncertainty_pack", "ocr_review_crop_archive_ui",
            "ocr_review_crop_uncertainty", "ocr_review_crop_uncertainty_live_ui",
            "ocr_crop_quality", "ocr_crop_advice",
        },
        "ocr_crop_quality": {"ocr_crop_advice"},
        "ocr_crop_advice": {"ocr_review_crop_archive_ui", "ocr_review_crop_uncertainty_live_ui"},
        "ocr_crop_uncertainty_journal": {
            "ocr_crop_uncertainty_comparison", "ocr_crop_uncertainty_pack", "ocr_review_crop_packs",
            "ocr_review_crop_uncertainty", "ocr_review_execution",
        },
        "ocr_crop_uncertainty_comparison": {"ocr_crop_uncertainty_pack", "ocr_review_crop_packs", "ocr_review_execution"},
        "ocr_crop_uncertainty_pack": {"ocr_crop_review_pack_io", "ocr_review_crop_packs"},
        "ocr_review_crop_uncertainty": {"ocr_review_crop_archive_ui", "ocr_review_crop_uncertainty_live_ui"},
        "ocr_review_crop_uncertainty_editor": {"ocr_review_crop_archive_ui", "ocr_review_crop_uncertainty_live_ui"},
        "ocr_review_crop_uncertainty_live_ui": {"ocr_review_crop_ui"},
        "ocr_review_crop_ui_common": {
            "ocr_review_crop_archive_ui", "ocr_review_crop_ui", "ocr_review_crop_uncertainty_live_ui",
            "ocr_spot_audit_ui",
        },
        "ocr_review_save_feedback": {"ocr_review_crop_archive_ui", "ocr_review_crop_uncertainty_live_ui"},
        "ocr_spot_audit": {"ocr_review_runtime", "ocr_spot_audit_ui"},
        "ocr_spot_audit_ui": {"ocr_review_ui"},
        "ocr_review_ui": {"tools.review_ocr"},
        "tools.review_ocr": set(),
    }
    forbidden = {
        "rag", "job_manager", "job_application", "application_composition",
        "ai_pipeline_client", "service_api", "service_runtime",
        "service_evidence_http", "service_evidence_runtime", "service_evidence_search",
    }
    for module, dependencies in expected.items():
        assert graph[module] == dependencies
        assert not forbidden & _transitive_dependencies(graph, module)
    # Validator reuse gives comparison a transitive IO graph. This pins its
    # existing layering, not a false claim that comparison is a dependency leaf.
    assert "ocr_hardscan_io" in _transitive_dependencies(graph, "ocr_crop_comparison")
    # Historical validation reuses pure entrypoints in existing IO/runtime
    # modules. Pin that honest static closure, not a false dependency-leaf claim.
    assert {"ocr_hardscan_io", "ocr_experiment_runtime"} <= _transitive_dependencies(graph, "ocr_disposition_archive")
    for archive in ("ocr_disposition_archive", "ocr_crop_review_pack", "ocr_crop_review_pack_io",
                    "ocr_crop_raster_view", "ocr_crop_uncertainty_journal",
                    "ocr_crop_uncertainty_comparison", "ocr_crop_uncertainty_pack", "ocr_spot_audit",
                    "ocr_crop_quality", "ocr_crop_advice", "ocr_context_authoring"):
        assert not {
            "ocr_review_execution", "ocr_review_crop_packs", "ocr_review_crop_archive_ui",
            "ocr_review_ui", "tools.review_ocr", "ocr_crop_preview_supervision",
            "ocr_detection_disposition_io", "ocr_detection_disposition_runtime",
            "ocr_review_crop_ui", "ocr_review_crop_ui_common", "ocr_review_crop_preview_ui",
            "ocr_review_crop_uncertainty", "ocr_review_crop_uncertainty_editor", "ocr_review_crop_uncertainty_live_ui",
            "ocr_review_save_feedback", "ocr_spot_audit_ui", "ocr_review_context_ui",
        } & _transitive_dependencies(graph, archive)
    # The shared UI state/field seam is inward. Neither a lazy import nor a
    # compatibility alias permits it to reach a panel or live host again.
    assert not {
        "ocr_review_crop_ui", "ocr_review_crop_archive_ui", "ocr_review_crop_uncertainty_live_ui",
        "ocr_review_execution", "ocr_review_crop_packs", "ocr_review_ui", "tools.review_ocr",
        "ocr_review_save_feedback", "ocr_spot_audit_ui", "ocr_review_context_ui",
    } & _transitive_dependencies(graph, "ocr_review_crop_ui_common")


@pytest.mark.parametrize("module", (
    "ocr_crop_comparison", "ocr_crop_review_runtime", "ocr_crop_preview_worker",
    "ocr_crop_preview_supervision", "ocr_review_crop_ui", "ocr_review_crop_preview_ui",
    "ocr_review_execution", "ocr_review_execution_ui", "ocr_review_ui", "tools.review_ocr",
    "ocr_disposition_archive", "ocr_crop_review_pack", "ocr_crop_review_pack_io",
    "ocr_review_crop_packs", "ocr_review_crop_archive_ui",
    "ocr_crop_raster_view", "ocr_crop_uncertainty_journal", "ocr_crop_uncertainty_comparison",
    "ocr_crop_uncertainty_pack", "ocr_review_crop_uncertainty", "ocr_review_crop_uncertainty_editor",
    "ocr_review_crop_uncertainty_live_ui", "ocr_review_crop_ui_common",
    "ocr_review_save_feedback", "ocr_spot_audit", "ocr_spot_audit_ui",
    "ocr_crop_quality", "ocr_crop_advice",
    "ocr_context_authoring", "ocr_review_context_ui", "ocr_review_runtime", "ocr_review_drafts",
))
def test_guided_ocr_review_imports_without_attempting_heavy_backends(module):
    result = _run_isolated(f'''
forbidden = {{
    "gradio", "pymupdf", "fitz", "PIL", "rapidocr", "cv2", "numpy",
    "onnxruntime", "torch", "transformers", "docling", "chromadb", "qdrant_client",
    "fastapi", "starlette", "rag", "job_manager", "service_api", "service_runtime",
    "application_composition",
}}
assert not forbidden.intersection(name.split(".", 1)[0] for name in sys.modules)
attempts = []
class RefuseHeavyImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in forbidden:
            attempts.append(fullname)
            raise ImportError("heavy imports forbidden in isolated architecture check")
sys.meta_path.insert(0, RefuseHeavyImports())
__import__({module!r})
assert not attempts, attempts
assert not forbidden.intersection(name.split(".", 1)[0] for name in sys.modules)
''')
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module,first_party,standard_library,optional", (
    ("ocr_context_evaluation", set(), {"math", "re"}, set()),
    ("ocr_context_authoring", {"ocr_context_evaluation"}, {"copy"}, set()),
    ("ocr_review_context_ui", {
        "ocr_context_authoring", "ocr_context_evaluation", "ocr_review", "ocr_review_runtime",
    }, {"copy", "hashlib", "json", "uuid"}, set()),
    ("ocr_review_drafts", {
        "evaluation_inputs", "ocr_context_authoring", "ocr_docling", "ocr_layout_assignment",
        "ocr_omission", "ocr_regions", "ocr_review",
    }, {"copy"}, set()),
    ("ocr_review_runtime", {
        "evaluation_inputs", "ocr_column_suggestions", "ocr_context_authoring", "ocr_context_evaluation",
        "ocr_context_evaluation_io", "ocr_docling", "ocr_layout", "ocr_layout_assignment", "ocr_omission",
        "ocr_recovery", "ocr_recovery_comparison", "ocr_regions", "ocr_review", "ocr_review_drafts",
        "ocr_scan_review", "ocr_spot_audit", "resource_lease", "storage_policy",
    }, {"copy", "dataclasses", "json", "math", "pathlib", "re", "stat", "uuid"}, {"PIL", "cv2", "numpy", "pymupdf"}),
    ("ocr_disposition_archive", {
        "evaluation_inputs", "model_artifacts", "ocr_checkpoint", "ocr_detection_disposition",
        "ocr_disposition_geometry", "ocr_execution_receipt", "ocr_experiment_runtime",
        "ocr_hardscan", "ocr_hardscan_io", "ocr_recovery_comparison", "ocr_regions",
    }, {"hashlib", "json", "math", "re", "types"}, set()),
    ("ocr_crop_review_pack", {"evaluation_inputs", "ocr_crop_comparison", "ocr_disposition_archive"},
     {"hashlib", "json", "types"}, set()),
    ("ocr_crop_review_pack_io", {
        "evaluation_inputs", "ocr_crop_review_pack", "ocr_crop_uncertainty_pack",
        "ocr_recovery", "resource_lease", "storage_policy",
    }, {"dataclasses", "hashlib", "os", "pathlib", "re", "stat", "threading", "uuid"}, set()),
    ("ocr_review_crop_packs", {
        "ocr_crop_comparison", "ocr_crop_preview_supervision", "ocr_crop_review_pack",
        "ocr_crop_review_pack_io", "ocr_crop_review_runtime", "ocr_review_runtime",
        "ocr_crop_uncertainty_comparison", "ocr_crop_uncertainty_journal", "ocr_crop_uncertainty_pack",
    }, {"functools", "json", "math", "pathlib", "threading", "time"}, set()),
    ("ocr_review_crop_archive_ui", {
        "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_review_runtime", "ocr_review_crop_preview_ui",
        "ocr_review_crop_ui_common", "ocr_review_crop_uncertainty", "ocr_review_crop_uncertainty_editor",
        "ocr_review_save_feedback", "ocr_crop_advice",
    },
     {"copy", "hashlib", "json", "re", "threading", "uuid"}, {"PIL", "gradio"}),
    ("ocr_review_crop_preview_ui", {"ocr_crop_review_runtime"}, set(), {"PIL", "gradio"}),
    ("ocr_crop_raster_view", {"ocr_crop_comparison"}, {"hashlib", "math", "struct"}, set()),
    ("ocr_crop_quality", {"ocr_crop_comparison", "ocr_crop_raster_view"},
     {"hashlib", "json", "math"}, set()),
    ("ocr_crop_advice", {"ocr_crop_comparison", "ocr_crop_quality", "ocr_crop_raster_view"},
     {"fractions", "hashlib", "math"}, set()),
    ("ocr_crop_uncertainty_journal", {"ocr_crop_comparison", "ocr_crop_raster_view", "ocr_evaluation"},
     {"hashlib", "json", "math", "re"}, set()),
    ("ocr_crop_uncertainty_comparison", {
        "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_uncertainty_journal",
    }, {"hashlib", "json"}, set()),
    ("ocr_crop_uncertainty_pack", {
        "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_review_pack",
        "ocr_crop_uncertainty_comparison", "ocr_crop_uncertainty_journal",
    }, {"json"}, set()),
    ("ocr_review_crop_uncertainty", {"ocr_crop_raster_view", "ocr_crop_uncertainty_journal", "ocr_evaluation"},
     {"hashlib", "json"}, set()),
    ("ocr_review_crop_uncertainty_editor", {"ocr_review_crop_preview_ui"},
     {"json", "math", "re"}, {"gradio"}),
    ("ocr_review_crop_uncertainty_live_ui", {
        "ocr_crop_comparison", "ocr_crop_raster_view", "ocr_crop_review_runtime", "ocr_review_crop_preview_ui",
        "ocr_review_crop_ui_common", "ocr_review_crop_uncertainty", "ocr_review_crop_uncertainty_editor",
        "ocr_review_save_feedback", "ocr_crop_advice",
    }, {"copy", "hashlib", "json", "uuid"}, {"PIL", "gradio"}),
    ("ocr_review_save_feedback", set(), {"json"}, {"gradio"}),
    ("ocr_spot_audit", {"ocr_evaluation", "ocr_review"},
     {"copy", "hashlib", "json", "math", "re"}, set()),
    ("ocr_spot_audit_ui", {"ocr_review_crop_ui_common", "ocr_spot_audit"},
     {"copy", "hashlib", "json", "secrets", "sys", "threading"}, {"gradio"}),
    ("ocr_review_crop_ui_common", {"ocr_crop_comparison"},
     {"hashlib", "re", "struct", "threading", "uuid"}, {"PIL"}),
    ("ocr_review_crop_ui", {
        "ocr_crop_comparison", "ocr_crop_review_pack_io", "ocr_crop_review_runtime", "ocr_review_crop_preview_ui",
        "ocr_review_crop_ui_common", "ocr_review_crop_uncertainty_live_ui",
    }, {"hashlib", "json", "re", "uuid"}, {"gradio"}),
))
def test_crop_pack_direct_imports_have_only_explicit_stdlib_and_lazy_ui_dependencies(
        module, first_party, standard_library, optional):
    # Fixed-path import checks may inspect a development file before it is
    # tracked; the exact graph test above independently refuses that cohort.
    assert standard_library <= sys.stdlib_module_names
    assert _raw_import_roots(PROJECT_ROOT / f"{module}.py") == first_party | standard_library | optional


@pytest.mark.parametrize("module", (
    "ocr_disposition_archive", "ocr_crop_review_pack", "ocr_crop_review_pack_io",
    "ocr_crop_raster_view", "ocr_crop_uncertainty_journal", "ocr_crop_uncertainty_comparison",
    "ocr_crop_uncertainty_pack", "ocr_review_crop_uncertainty",
    "ocr_spot_audit", "ocr_context_authoring",
    "ocr_crop_quality", "ocr_crop_advice",
))
def test_crop_pack_archive_import_does_not_attempt_live_host_or_dispatch(module):
    result = _run_isolated(f'''
forbidden = {{
    "ocr_review_execution", "ocr_review_crop_packs", "ocr_review_crop_archive_ui",
    "ocr_review_crop_ui", "ocr_review_execution_ui", "ocr_review_ui", "ocr_review_runtime",
    "ocr_crop_preview_supervision", "ocr_crop_preview_worker",
    "ocr_detection_disposition_io", "ocr_detection_disposition_runtime", "tools.review_ocr",
    "ocr_review_crop_ui_common", "ocr_review_crop_preview_ui",
    "ocr_review_crop_uncertainty_editor", "ocr_review_crop_uncertainty_live_ui",
    "ocr_review_save_feedback", "ocr_spot_audit_ui", "ocr_review_context_ui",
}}
def denied(name):
    return any(name == item or name.startswith(item + ".") for item in forbidden)
assert not [name for name in sys.modules if denied(name)]
attempts = []
class RefuseLiveCropHost:
    def find_spec(self, fullname, path=None, target=None):
        if denied(fullname):
            attempts.append(fullname)
            raise ImportError("live host imports forbidden in historical architecture check")
sys.meta_path.insert(0, RefuseLiveCropHost())
__import__({module!r})
assert not attempts, attempts
assert not [name for name in sys.modules if denied(name)]
''')
    assert result.returncode == 0, result.stderr


def test_shared_crop_ui_import_does_not_attempt_a_panel_or_live_host():
    result = _run_isolated('''
forbidden = {
    "ocr_review_crop_ui", "ocr_review_crop_archive_ui", "ocr_review_crop_uncertainty_live_ui",
    "ocr_review_crop_uncertainty_editor", "ocr_review_crop_preview_ui",
    "ocr_review_execution", "ocr_review_execution_ui", "ocr_review_crop_packs",
    "ocr_review_runtime", "ocr_crop_review_runtime", "ocr_review_ui", "tools.review_ocr",
    "ocr_crop_preview_supervision", "ocr_crop_preview_worker",
    "ocr_review_save_feedback", "ocr_spot_audit_ui", "ocr_review_context_ui",
}
def denied(name):
    return any(name == item or name.startswith(item + ".") for item in forbidden)
assert not [name for name in sys.modules if denied(name)]
attempts = []
class RefuseCropPanels:
    def find_spec(self, fullname, path=None, target=None):
        if denied(fullname):
            attempts.append(fullname)
            raise ImportError("outward UI imports forbidden in shared-helper architecture check")
sys.meta_path.insert(0, RefuseCropPanels())
__import__("ocr_review_crop_ui_common")
assert not attempts, attempts
assert not [name for name in sys.modules if denied(name)]
''')
    assert result.returncode == 0, result.stderr
