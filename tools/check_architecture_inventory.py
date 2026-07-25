#!/usr/bin/env python3
"""Generate or check the deterministic Python architecture inventory."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = Path("architecture-inventory.json")
SCHEMA_VERSION = 1
ANALYSIS_MODEL = "tracked-python-ast-v1"
_GROUP_NAMES = ("production", "tests", "tools", "other")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.check_python_sources import (  # noqa: E402
    SourceGateError,
    tracked_python_paths,
    validate_source_paths,
)


class ArchitectureInventoryError(RuntimeError):
    """An inventory source, schema, or baseline invariant failed."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _module_name(path: Path) -> str:
    parts = path.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        raise ArchitectureInventoryError(
            f"Python source has no importable module identity: {path.as_posix()}"
        )
    return ".".join(parts)


def _source_group(path: Path) -> str:
    if path.parts[0] == "tests":
        return "tests"
    if path.parts[0] == "tools":
        return "tools"
    if path.parts[0] == "scripts":
        return "other"
    return "production"


def _read_tree(root: Path, relative: Path) -> tuple[str, ast.Module]:
    try:
        source = (root / relative).read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=relative.as_posix())
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise ArchitectureInventoryError(
            f"Cannot parse tracked Python source {relative.as_posix()}: {exc}"
        ) from exc
    return source, tree


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
    current: str,
    path: Path,
    node: ast.Import | ast.ImportFrom,
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


def _strongly_connected_components(
    graph: Mapping[str, set[str]],
) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []

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
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        result.append(sorted(component))

    for module in sorted(graph):
        if module not in indices:
            visit(module)
    return sorted(result, key=lambda component: tuple(component))


def _parameter_inventory(arguments: ast.arguments) -> dict[str, object]:
    positional = [*arguments.posonlyargs, *arguments.args]
    required_positional = len(positional) - len(arguments.defaults)
    required_keyword_only = sum(
        default is None for default in arguments.kw_defaults
    )
    named = len(positional) + len(arguments.kwonlyargs)
    return {
        "accepts_var_keyword": arguments.kwarg is not None,
        "accepts_var_positional": arguments.vararg is not None,
        "keyword_only": len(arguments.kwonlyargs),
        "positional_only": len(arguments.posonlyargs),
        "positional_or_keyword": len(arguments.args),
        "required": required_positional + required_keyword_only,
        "total": (
            named
            + int(arguments.vararg is not None)
            + int(arguments.kwarg is not None)
        ),
    }


class _DefinitionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[tuple[str, str]] = []
        self.functions: list[dict[str, object]] = []
        self.classes: list[dict[str, object]] = []

    @staticmethod
    def _first_line(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
        lines = [node.lineno]
        lines.extend(decorator.lineno for decorator in node.decorator_list)
        return min(lines)

    def _qualified_name(self, name: str) -> str:
        return ".".join([*(item[0] for item in self.scope), name])

    def _scope_kind(self) -> str:
        if not self.scope:
            return "top_level"
        if self.scope[-1][1] == "class":
            return "class"
        return "nested"

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str,
    ) -> None:
        qualified = self._qualified_name(node.name)
        first_line = self._first_line(node)
        last_line = node.end_lineno or node.lineno
        self.functions.append({
            "arity": _parameter_inventory(node.args),
            "kind": kind,
            "name": qualified,
            "scope": self._scope_kind(),
            "span": [first_line, last_line],
        })
        self.scope.append((node.name, "function"))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, "async_function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = self._qualified_name(node.name)
        first_line = self._first_line(node)
        last_line = node.end_lineno or node.lineno
        self.classes.append({
            "name": qualified,
            "scope": self._scope_kind(),
            "span": [first_line, last_line],
        })
        self.scope.append((node.name, "class"))
        self.generic_visit(node)
        self.scope.pop()


def _definitions(
    tree: ast.Module,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    visitor = _DefinitionVisitor()
    visitor.visit(tree)
    def key(item: dict[str, object]) -> tuple[object, object, object]:
        return (
            item["span"][0], item["name"], item.get("kind", "")
        )

    return sorted(visitor.functions, key=key), sorted(visitor.classes, key=key)


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in _target_names(item)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _pattern_names(pattern: ast.pattern) -> list[str]:
    if isinstance(pattern, ast.MatchAs):
        result = [pattern.name] if pattern.name else []
        if pattern.pattern is not None:
            result.extend(_pattern_names(pattern.pattern))
        return result
    if isinstance(pattern, ast.MatchStar):
        return [pattern.name] if pattern.name else []
    if isinstance(pattern, ast.MatchMapping):
        result = [pattern.rest] if pattern.rest else []
        for nested in pattern.patterns:
            result.extend(_pattern_names(nested))
        return result
    if isinstance(pattern, ast.MatchSequence):
        return [name for item in pattern.patterns for name in _pattern_names(item)]
    if isinstance(pattern, ast.MatchClass):
        return [
            name
            for item in [*pattern.patterns, *pattern.kwd_patterns]
            for name in _pattern_names(item)
        ]
    if isinstance(pattern, ast.MatchOr):
        return [name for item in pattern.patterns for name in _pattern_names(item)]
    return []


def _module_binding_origins(tree: ast.Module) -> dict[str, list[dict[str, object]]]:
    origins: dict[str, list[dict[str, object]]] = defaultdict(list)
    initializers = {
        statement.name: statement
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    class InitializerGlobals(ast.NodeVisitor):
        def __init__(self) -> None:
            self.declared: set[str] = set()
            self.assigned: dict[str, int] = {}

        def visit_Global(self, node: ast.Global) -> None:
            self.declared.update(node.names)

        def _record(self, target: ast.AST, line: int) -> None:
            for name in _target_names(target):
                self.assigned.setdefault(name, line)

        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                self._record(target, node.lineno)
            self.generic_visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            self._record(node.target, node.lineno)
            if node.value is not None:
                self.generic_visit(node.value)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            self._record(node.target, node.lineno)
            self.generic_visit(node.value)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    def initializer_writes(name: str) -> list[tuple[str, int]]:
        definition = initializers.get(name)
        if definition is None:
            return []
        visitor = InitializerGlobals()
        for nested in definition.body:
            visitor.visit(nested)
        return sorted(
            (global_name, visitor.assigned[global_name])
            for global_name in visitor.declared & visitor.assigned.keys()
        )

    def is_main_guard(node: ast.AST) -> bool:
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            return False
        if not isinstance(node.ops[0], ast.Eq) or len(node.comparators) != 1:
            return False
        operands = (node.left, node.comparators[0])
        return any(
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
            for left, right in (operands, tuple(reversed(operands)))
        )

    def add(name: str | None, kind: str, line: int, **extra: object) -> None:
        if not name:
            return
        record: dict[str, object] = {"kind": kind, "line": line}
        record.update(extra)
        if record not in origins[name]:
            origins[name].append(record)

    def visit(statements: Iterable[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    binding = alias.asname or alias.name.split(".", 1)[0]
                    add(binding, "import", statement.lineno, source=alias.name)
            elif isinstance(statement, ast.ImportFrom):
                source = "." * statement.level + (statement.module or "")
                for alias in statement.names:
                    if alias.name == "*":
                        add("*", "star_import", statement.lineno, source=source)
                    else:
                        add(
                            alias.asname or alias.name,
                            "from_import",
                            statement.lineno,
                            imported=alias.name,
                            source=source,
                        )
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add(statement.name, "definition", statement.lineno)
            elif isinstance(statement, ast.ClassDef):
                add(statement.name, "class_definition", statement.lineno)
            elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    for name in _target_names(target):
                        add(name, "assignment", statement.lineno)
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                for name in _target_names(statement.target):
                    add(name, "loop_target", statement.lineno)
                visit(statement.body)
                visit(statement.orelse)
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    if item.optional_vars is not None:
                        for name in _target_names(item.optional_vars):
                            add(name, "with_target", statement.lineno)
                visit(statement.body)
            elif isinstance(statement, ast.If):
                if not is_main_guard(statement.test):
                    visit(statement.body)
                visit(statement.orelse)
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                visit(statement.body)
                for handler in statement.handlers:
                    visit(handler.body)
                visit(statement.orelse)
                visit(statement.finalbody)
            elif isinstance(statement, ast.While):
                visit(statement.body)
                visit(statement.orelse)
            elif isinstance(statement, ast.Match):
                for case in statement.cases:
                    for name in _pattern_names(case.pattern):
                        add(name, "match_target", statement.lineno)
                    visit(case.body)
            elif (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
            ):
                initializer = statement.value.func.id
                for name, assignment_line in initializer_writes(initializer):
                    add(
                        name,
                        "initializer_global",
                        statement.lineno,
                        assignment_line=assignment_line,
                        initializer=initializer,
                    )

    visit(tree.body)
    return {
        name: sorted(
            records,
            key=lambda item: (
                item["line"], item["kind"], str(item.get("source", "")),
                str(item.get("imported", "")),
            ),
        )
        for name, records in sorted(origins.items())
    }


def _literal_all(tree: ast.Module) -> tuple[bool, list[str]]:
    values: list[str] | None = None
    for statement in tree.body:
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign):
            if any(
                isinstance(item, ast.Name) and item.id == "__all__"
                for item in statement.targets
            ):
                target = statement.targets[0]
                value = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "__all__"
        ):
            target = statement.target
            value = statement.value
        if target is None:
            continue
        try:
            raw = ast.literal_eval(value) if value is not None else None
        except (ValueError, TypeError, SyntaxError) as exc:
            raise ArchitectureInventoryError(
                "rag.__all__ must be one literal string list or tuple"
            ) from exc
        if (
            not isinstance(raw, (list, tuple))
            or not all(isinstance(item, str) and item for item in raw)
        ):
            raise ArchitectureInventoryError(
                "rag.__all__ must be one literal string list or tuple"
            )
        values = list(raw)
    if values is None:
        return False, []
    if len(values) != len(set(values)):
        raise ArchitectureInventoryError("rag.__all__ contains duplicate names")
    return True, values


def _attribute_chain(node: ast.AST) -> tuple[str, list[str]] | None:
    attributes: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    return current.id, list(reversed(attributes))


def _rag_aliases(tree: ast.Module) -> tuple[set[str], list[dict[str, object]], list[tuple[str, str, int]]]:
    aliases: set[str] = set()
    imports: list[dict[str, object]] = []
    from_imports: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "rag":
                    binding = alias.asname or "rag"
                    aliases.add(binding)
                    imports.append({
                        "binding": binding,
                        "kind": "import",
                        "line": node.lineno,
                    })
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "rag":
            for alias in node.names:
                binding = alias.asname or alias.name
                imports.append({
                    "binding": binding,
                    "imported": alias.name,
                    "kind": "from_import",
                    "line": node.lineno,
                })
                from_imports.append((alias.name, binding, node.lineno))
    def key(item: dict[str, object]) -> tuple[object, object, object, object]:
        return (
            item["line"], item["kind"], item["binding"],
            item.get("imported", ""),
        )

    return aliases, sorted(imports, key=key), sorted(from_imports, key=lambda item: (item[2], item[0], item[1]))


def _rag_attribute_references(
    tree: ast.Module, aliases: set[str], path: str,
) -> list[dict[str, object]]:
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    references: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue
        chain = _attribute_chain(node)
        if chain is None or chain[0] not in aliases or not chain[1]:
            continue
        context = type(node.ctx).__name__.lower()
        references.append({
            "context": context,
            "kind": "attribute",
            "line": node.lineno,
            "name": chain[1][0],
            "path": ".".join(chain[1]),
            "source": path,
        })
    return sorted(
        references,
        key=lambda item: (
            item["source"], item["line"], item["path"], item["context"]
        ),
    )


def _patch_target(
    call: ast.Call, aliases: set[str],
) -> tuple[str, str] | None:
    function_name = (
        call.func.id
        if isinstance(call.func, ast.Name)
        else call.func.attr
        if isinstance(call.func, ast.Attribute)
        else ""
    )
    if function_name in {"setattr", "delattr"}:
        if not call.args:
            return None
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if first.value.startswith("rag."):
                return function_name, first.value[4:]
            return None
        chain = _attribute_chain(first)
        if chain is None or chain[0] not in aliases:
            return None
        if len(call.args) < 2 or not isinstance(call.args[1], ast.Constant):
            return None
        attribute = call.args[1].value
        if not isinstance(attribute, str) or not attribute:
            return None
        return function_name, ".".join([*chain[1], attribute])

    if function_name == "patch":
        if call.args and isinstance(call.args[0], ast.Constant):
            value = call.args[0].value
            if isinstance(value, str) and value.startswith("rag."):
                return "patch", value[4:]
        return None

    if function_name == "object" and isinstance(call.func, ast.Attribute):
        owner = call.func.value
        owner_chain = _attribute_chain(owner)
        is_patch_object = (
            isinstance(owner, ast.Name) and owner.id == "patch"
        ) or (
            owner_chain is not None
            and bool(owner_chain[1])
            and owner_chain[1][-1] == "patch"
        )
        if not is_patch_object or len(call.args) < 2:
            return None
        target_chain = _attribute_chain(call.args[0])
        attribute = call.args[1]
        if (
            target_chain is None
            or target_chain[0] not in aliases
            or not isinstance(attribute, ast.Constant)
            or not isinstance(attribute.value, str)
        ):
            return None
        return "patch.object", ".".join([*target_chain[1], attribute.value])
    return None


def _patch_inventory(
    trees: Mapping[Path, ast.Module],
) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    dynamic: list[dict[str, object]] = []
    for relative in sorted(trees, key=lambda item: item.as_posix()):
        tree = trees[relative]
        aliases, _imports, _from_imports = _rag_aliases(tree)
        if not aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _patch_target(node, aliases)
            if target is not None:
                operation, dotted = target
                record = {
                    "line": node.lineno,
                    "operation": operation,
                    "source": relative.as_posix(),
                }
                if record not in grouped[dotted]:
                    grouped[dotted].append(record)
                continue
            function_name = (
                node.func.attr if isinstance(node.func, ast.Attribute) else ""
            )
            if function_name not in {"setattr", "delattr"} or not node.args:
                continue
            chain = _attribute_chain(node.args[0])
            if chain is None or chain[0] not in aliases:
                continue
            dynamic.append({
                "line": node.lineno,
                "operation": function_name,
                "source": relative.as_posix(),
            })

    top_level: list[dict[str, object]] = []
    nested: list[dict[str, object]] = []
    for target, occurrences in sorted(grouped.items()):
        locations = sorted(
            occurrences,
            key=lambda item: (item["source"], item["line"], item["operation"]),
        )
        record = {
            "locations_sha256": _canonical_sha256(locations),
            "occurrence_count": len(locations),
            "operations": sorted({item["operation"] for item in occurrences}),
            "sources": sorted({str(item["source"]) for item in occurrences}),
            "target": target,
        }
        (nested if "." in target else top_level).append(record)
    return {
        "dynamic_sites": sorted(
            dynamic,
            key=lambda item: (item["source"], item["line"], item["operation"]),
        ),
        "nested": nested,
        "top_level": top_level,
    }


def _reference_groups(
    references: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for reference in references:
        name = str(reference["name"])
        location = {
            "context": reference["context"],
            "kind": reference["kind"],
            "line": reference["line"],
            "path": reference["path"],
            "source": reference["source"],
        }
        if location not in grouped[name]:
            grouped[name].append(location)

    result: list[dict[str, object]] = []
    for name in sorted(grouped):
        locations = sorted(
            grouped[name],
            key=lambda item: (
                item["source"], item["line"], item["path"],
                item["kind"], item["context"],
            ),
        )
        result.append({
            "contexts": sorted({str(item["context"]) for item in locations}),
            "kinds": sorted({str(item["kind"]) for item in locations}),
            "locations_sha256": _canonical_sha256(locations),
            "name": name,
            "occurrence_count": len(locations),
            "paths": sorted({str(item["path"]) for item in locations}),
            "sources": sorted({str(item["source"]) for item in locations}),
        })
    return result


def _consumer_inventory(
    module: str, path: Path, tree: ast.Module,
) -> dict[str, object] | None:
    aliases, imports, from_imports = _rag_aliases(tree)
    if not imports:
        return None
    references = _rag_attribute_references(tree, aliases, path.as_posix())
    for imported, _binding, line in from_imports:
        references.append({
            "context": "load",
            "kind": "from_import",
            "line": line,
            "name": imported.split(".", 1)[0],
            "path": imported,
            "source": path.as_posix(),
        })
    references.sort(
        key=lambda item: (
            item["source"], item["line"], item["path"], item["kind"],
            item["context"],
        )
    )
    return {
        "imports": imports,
        "module": module,
        "path": path.as_posix(),
        "references": _reference_groups(references),
    }


def _imported_binding_inventory(
    origins: Mapping[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for binding, records in origins.items():
        for origin in records:
            if origin["kind"] not in {"import", "from_import"}:
                continue
            record = {"binding": binding, **origin}
            result.append(record)
    return sorted(
        result,
        key=lambda item: (
            item["line"], item["binding"], item["kind"], item["source"],
            str(item.get("imported", "")),
        ),
    )


def build_inventory(root: Path = PROJECT_ROOT) -> dict[str, object]:
    """Build the complete content-free AST inventory below *root*."""
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ArchitectureInventoryError(f"Repository root is invalid: {root}") from exc
    if not resolved_root.is_dir():
        raise ArchitectureInventoryError(f"Repository root is not a directory: {root}")
    try:
        relative_paths = tracked_python_paths(resolved_root)
        relative_paths = validate_source_paths(resolved_root, relative_paths)
    except (OSError, SourceGateError) as exc:
        raise ArchitectureInventoryError(str(exc)) from exc

    groups: dict[str, list[Path]] = {name: [] for name in _GROUP_NAMES}
    trees: dict[Path, ast.Module] = {}
    sources: dict[Path, str] = {}
    for relative in relative_paths:
        groups[_source_group(relative)].append(relative)
        source, tree = _read_tree(resolved_root, relative)
        sources[relative] = source
        trees[relative] = tree

    production_paths = groups["production"]
    modules: dict[str, Path] = {}
    for relative in production_paths:
        module = _module_name(relative)
        if module in modules:
            raise ArchitectureInventoryError(
                f"Duplicate production module identity: {module}"
            )
        modules[module] = relative
    if "rag" not in modules or modules["rag"].as_posix() != "rag.py":
        raise ArchitectureInventoryError("The tracked production rag.py facade is missing")

    known = set(modules)
    graph: dict[str, set[str]] = {module: set() for module in modules}
    module_records: list[dict[str, object]] = []
    for module, relative in sorted(modules.items()):
        tree = trees[relative]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                graph[module].update(
                    _import_targets(module, relative, node, known)
                )
        functions, classes = _definitions(tree)
        source = sources[relative]
        module_records.append({
            "class_count": len(classes),
            "classes": classes,
            "direct_dependencies": sorted(graph[module]),
            "function_count": len(functions),
            "functions": functions,
            "module": module,
            "non_blank_lines": sum(bool(line.strip()) for line in source.splitlines()),
            "path": relative.as_posix(),
            "physical_lines": len(source.splitlines()),
            "top_level_function_count": sum(
                item["scope"] == "top_level" for item in functions
            ),
        })

    components = _strongly_connected_components(graph)
    cyclic = [
        component
        for component in components
        if len(component) > 1 or component[0] in graph[component[0]]
    ]
    graph_nodes = [
        {
            "dependencies": sorted(graph[module]),
            "module": module,
            "path": modules[module].as_posix(),
        }
        for module in sorted(modules)
    ]

    rag_tree = trees[modules["rag"]]
    rag_origins = _module_binding_origins(rag_tree)
    if "*" in rag_origins:
        raise ArchitectureInventoryError(
            "rag.py uses a star import, so its static namespace is incomplete"
        )
    explicit_bindings = sorted(rag_origins)
    uses_explicit_all, explicit_all = _literal_all(rag_tree)
    import_star_names = (
        sorted(explicit_all)
        if uses_explicit_all
        else [name for name in explicit_bindings if not name.startswith("_")]
    )
    rag_functions, rag_classes = _definitions(rag_tree)
    local_definitions = sorted(
        [
            *(
                {
                    "kind": item["kind"],
                    "name": item["name"],
                    "span": item["span"],
                }
                for item in rag_functions
                if item["scope"] == "top_level"
            ),
            *(
                {
                    "kind": "class",
                    "name": item["name"],
                    "span": item["span"],
                }
                for item in rag_classes
                if item["scope"] == "top_level"
            ),
        ],
        key=lambda item: (item["span"][0], item["name"]),
    )

    production_consumers: list[dict[str, object]] = []
    for module, relative in sorted(modules.items()):
        if module == "rag":
            continue
        consumer = _consumer_inventory(module, relative, trees[relative])
        if consumer is not None:
            production_consumers.append(consumer)

    test_consumers: list[dict[str, object]] = []
    private_references: list[dict[str, object]] = []
    test_trees = {path: trees[path] for path in groups["tests"]}
    for relative in sorted(groups["tests"], key=lambda item: item.as_posix()):
        aliases, _imports, from_imports = _rag_aliases(trees[relative])
        consumer = _consumer_inventory(
            _module_name(relative), relative, trees[relative]
        )
        if consumer is None:
            continue
        test_consumers.append({
            "imports": consumer["imports"],
            "module": consumer["module"],
            "path": consumer["path"],
        })
        direct_references = _rag_attribute_references(
            trees[relative], aliases, relative.as_posix()
        )
        direct_references.extend({
            "context": "load",
            "kind": "from_import",
            "line": line,
            "name": imported.split(".", 1)[0],
            "path": imported,
            "source": relative.as_posix(),
        } for imported, _binding, line in from_imports)
        private_references.extend(
            reference
            for reference in direct_references
            if reference["name"].startswith("_")
        )

    inventory = {
        "analysis_model": ANALYSIS_MODEL,
        "consumers": {
            "production": production_consumers,
            "tests": {
                "importing_module_count": len(test_consumers),
                "modules": test_consumers,
                "monkeypatch_seams": _patch_inventory(test_trees),
                "private_references": _reference_groups(private_references),
            },
        },
        "production": {
            "class_count": sum(len(item["classes"]) for item in module_records),
            "function_count": sum(len(item["functions"]) for item in module_records),
            "import_graph": {
                "cyclic_components": cyclic,
                "edge_count": sum(len(dependencies) for dependencies in graph.values()),
                "nodes": graph_nodes,
                "strongly_connected_components": components,
            },
            "module_count": len(module_records),
            "modules": module_records,
            "physical_line_count": sum(item["physical_lines"] for item in module_records),
            "top_level_function_count": sum(
                item["top_level_function_count"] for item in module_records
            ),
        },
        "rag_facade": {
            "initializer_bindings": [
                {
                    "initializer": origin["initializer"],
                    "name": name,
                }
                for name, origins in rag_origins.items()
                for origin in origins
                if origin["kind"] == "initializer_global"
            ],
            "explicit_binding_count": len(explicit_bindings),
            "explicit_bindings": explicit_bindings,
            "import_star": {
                "names": import_star_names,
                "uses_explicit_all": uses_explicit_all,
            },
            "imported_bindings": _imported_binding_inventory(rag_origins),
            "local_definitions": local_definitions,
            "path": "rag.py",
        },
        "schema_version": SCHEMA_VERSION,
        "tracked_sources": {
            "count": len(relative_paths),
            "groups": [
                {
                    "count": len(groups[name]),
                    "name": name,
                    "paths": sorted(path.as_posix() for path in groups[name]),
                }
                for name in _GROUP_NAMES
            ],
        },
    }
    validate_inventory(inventory)
    return inventory


def _require_dict(
    value: object, expected_keys: set[str], context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArchitectureInventoryError(f"{context} must be an object")
    observed = set(value)
    if observed != expected_keys:
        missing = ", ".join(sorted(expected_keys - observed)) or "none"
        extra = ", ".join(sorted(observed - expected_keys)) or "none"
        raise ArchitectureInventoryError(
            f"{context} fields differ (missing: {missing}; extra: {extra})"
        )
    if not all(isinstance(key, str) for key in value):
        raise ArchitectureInventoryError(f"{context} keys must be strings")
    return value


def _require_list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ArchitectureInventoryError(f"{context} must be an array")
    return value


def _require_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArchitectureInventoryError(f"{context} must be a non-empty string")
    return value


def _require_int(value: object, context: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ArchitectureInventoryError(
            f"{context} must be an integer of at least {minimum}"
        )
    return value


def _require_bool(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise ArchitectureInventoryError(f"{context} must be a boolean")
    return value


def _validate_path(value: object, context: str) -> str:
    text = _require_string(value, context)
    path = Path(text)
    if (
        path.is_absolute()
        or bool(path.drive)
        or path.suffix != ".py"
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ArchitectureInventoryError(
            f"{context} must be a safe relative POSIX Python path"
        )
    return text


def _validate_sorted_strings(value: object, context: str) -> list[str]:
    result = _require_list(value, context)
    if not all(isinstance(item, str) and item for item in result):
        raise ArchitectureInventoryError(
            f"{context} must contain only non-empty strings"
        )
    if result != sorted(set(result)):
        raise ArchitectureInventoryError(
            f"{context} must be sorted and contain no duplicates"
        )
    return result


def _validate_sha256(value: object, context: str) -> str:
    text = _require_string(value, context)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ArchitectureInventoryError(f"{context} must be a lowercase SHA-256")
    return text


def _validate_parameters(value: object, context: str) -> None:
    record = _require_dict(value, {
        "accepts_var_keyword", "accepts_var_positional", "keyword_only",
        "positional_only", "positional_or_keyword", "required", "total",
    }, context)
    _require_bool(record["accepts_var_keyword"], f"{context}.accepts_var_keyword")
    _require_bool(record["accepts_var_positional"], f"{context}.accepts_var_positional")
    named = sum(
        _require_int(record[name], f"{context}.{name}")
        for name in ("keyword_only", "positional_only", "positional_or_keyword")
    )
    required = _require_int(record["required"], f"{context}.required")
    total = _require_int(record["total"], f"{context}.total")
    expected_total = (
        named
        + int(record["accepts_var_keyword"])
        + int(record["accepts_var_positional"])
    )
    if total != expected_total or required > named:
        raise ArchitectureInventoryError(f"{context} arity totals are inconsistent")


def _validate_definition(
    value: object, context: str, *, function: bool,
) -> None:
    keys = {"name", "scope", "span"}
    if function:
        keys.update({"arity", "kind"})
    record = _require_dict(value, keys, context)
    _require_string(record["name"], f"{context}.name")
    span = _require_list(record["span"], f"{context}.span")
    if len(span) != 2:
        raise ArchitectureInventoryError(f"{context}.span must have two lines")
    first = _require_int(span[0], f"{context}.span[0]", 1)
    _require_int(span[1], f"{context}.span[1]", first)
    if record["scope"] not in {"top_level", "class", "nested"}:
        raise ArchitectureInventoryError(f"{context}.scope is invalid")
    if function:
        if record["kind"] not in {"function", "async_function"}:
            raise ArchitectureInventoryError(f"{context}.kind is invalid")
        _validate_parameters(record["arity"], f"{context}.arity")


def _validate_import_record(value: object, context: str) -> None:
    if not isinstance(value, dict):
        raise ArchitectureInventoryError(f"{context} must be an object")
    kind = value.get("kind")
    expected = {"binding", "kind", "line", "source"}
    if kind == "from_import":
        expected.add("imported")
    record = _require_dict(value, expected, context)
    _require_string(record["binding"], f"{context}.binding")
    _require_string(record["source"], f"{context}.source")
    _require_int(record["line"], f"{context}.line", 1)
    if kind not in {"import", "from_import"}:
        raise ArchitectureInventoryError(f"{context}.kind is invalid")
    if kind == "from_import":
        _require_string(record["imported"], f"{context}.imported")


def _validate_reference_group(
    value: object, context: str, tracked_paths: set[str],
) -> None:
    record = _require_dict(value, {
        "contexts", "kinds", "locations_sha256", "name",
        "occurrence_count", "paths", "sources",
    }, context)
    name = _require_string(record["name"], f"{context}.name")
    paths = _validate_sorted_strings(record["paths"], f"{context}.paths")
    if not paths or any(path.split(".", 1)[0] != name for path in paths):
        raise ArchitectureInventoryError(f"{context} paths do not match its name")
    contexts = _validate_sorted_strings(
        record["contexts"], f"{context}.contexts"
    )
    if not contexts or not set(contexts) <= {"load", "store", "del"}:
        raise ArchitectureInventoryError(f"{context}.contexts is invalid")
    if not _validate_sorted_strings(record["kinds"], f"{context}.kinds"):
        raise ArchitectureInventoryError(f"{context}.kinds cannot be empty")
    _require_int(record["occurrence_count"], f"{context}.occurrence_count", 1)
    _validate_sha256(record["locations_sha256"], f"{context}.locations_sha256")
    sources = _validate_sorted_strings(record["sources"], f"{context}.sources")
    if not sources:
        raise ArchitectureInventoryError(f"{context}.sources cannot be empty")
    for index, raw_source in enumerate(sources):
        source = _validate_path(raw_source, f"{context}.sources[{index}]")
        if source not in tracked_paths:
            raise ArchitectureInventoryError(
                f"{context}.sources[{index}] is not tracked"
            )


def _validate_rag_imports(value: object, context: str) -> None:
    imports = _require_list(value, context)
    if not imports:
        raise ArchitectureInventoryError(f"{context} cannot be empty")
    for index, raw_import in enumerate(imports):
        label = f"{context}[{index}]"
        if not isinstance(raw_import, dict):
            raise ArchitectureInventoryError(f"{label} must be an object")
        kind = raw_import.get("kind")
        keys = {"binding", "kind", "line"}
        if kind == "from_import":
            keys.add("imported")
        item = _require_dict(raw_import, keys, label)
        _require_string(item["binding"], f"{label}.binding")
        _require_int(item["line"], f"{label}.line", 1)
        if kind not in {"import", "from_import"}:
            raise ArchitectureInventoryError(f"{label}.kind is invalid")
        if kind == "from_import":
            _require_string(item["imported"], f"{label}.imported")


def _validate_consumer(
    value: object,
    context: str,
    tracked_paths: set[str],
    known_modules: set[str] | None = None,
) -> None:
    record = _require_dict(value, {"imports", "module", "path", "references"}, context)
    module = _require_string(record["module"], f"{context}.module")
    if known_modules is not None and module not in known_modules:
        raise ArchitectureInventoryError(f"{context}.module is not production")
    path = _validate_path(record["path"], f"{context}.path")
    if path not in tracked_paths:
        raise ArchitectureInventoryError(f"{context}.path is not tracked")
    _validate_rag_imports(record["imports"], f"{context}.imports")
    references = _require_list(record["references"], f"{context}.references")
    names: list[str] = []
    for index, reference in enumerate(references):
        _validate_reference_group(
            reference, f"{context}.references[{index}]", tracked_paths
        )
        names.append(reference["name"])
    if names != sorted(set(names)):
        raise ArchitectureInventoryError(
            f"{context}.references must be sorted by unique name"
        )


def _validate_patch_occurrence(
    value: object, context: str, tracked_test_paths: set[str],
) -> None:
    record = _require_dict(value, {"line", "operation", "source"}, context)
    _require_int(record["line"], f"{context}.line", 1)
    if record["operation"] not in {"setattr", "delattr", "patch", "patch.object"}:
        raise ArchitectureInventoryError(f"{context}.operation is invalid")
    source = _validate_path(record["source"], f"{context}.source")
    if source not in tracked_test_paths:
        raise ArchitectureInventoryError(f"{context}.source is not a tracked test")


def validate_inventory(value: object) -> None:
    """Validate exact schema and all cross-field inventory invariants."""
    document = _require_dict(value, {
        "analysis_model", "consumers", "production", "rag_facade",
        "schema_version", "tracked_sources",
    }, "inventory")
    schema_version = _require_int(
        document["schema_version"], "inventory.schema_version", 1
    )
    if schema_version != SCHEMA_VERSION:
        raise ArchitectureInventoryError(
            f"inventory.schema_version must be {SCHEMA_VERSION}"
        )
    if document["analysis_model"] != ANALYSIS_MODEL:
        raise ArchitectureInventoryError(
            f"inventory.analysis_model must be {ANALYSIS_MODEL}"
        )

    tracked = _require_dict(
        document["tracked_sources"], {"count", "groups"}, "tracked_sources"
    )
    tracked_count = _require_int(tracked["count"], "tracked_sources.count", 1)
    raw_groups = _require_list(tracked["groups"], "tracked_sources.groups")
    if len(raw_groups) != len(_GROUP_NAMES):
        raise ArchitectureInventoryError("tracked_sources.groups is incomplete")
    grouped_paths: dict[str, list[str]] = {}
    all_paths: list[str] = []
    for index, (raw_group, expected_name) in enumerate(zip(raw_groups, _GROUP_NAMES)):
        context = f"tracked_sources.groups[{index}]"
        group = _require_dict(raw_group, {"count", "name", "paths"}, context)
        if group["name"] != expected_name:
            raise ArchitectureInventoryError(f"{context}.name must be {expected_name}")
        paths = _validate_sorted_strings(group["paths"], f"{context}.paths")
        for path_index, path in enumerate(paths):
            _validate_path(path, f"{context}.paths[{path_index}]")
        group_count = _require_int(group["count"], f"{context}.count")
        if group_count != len(paths):
            raise ArchitectureInventoryError(f"{context}.count is inconsistent")
        grouped_paths[expected_name] = paths
        all_paths.extend(paths)
    if len(all_paths) != len(set(all_paths)) or tracked_count != len(all_paths):
        raise ArchitectureInventoryError("tracked source counts/paths are inconsistent")
    tracked_paths = set(all_paths)

    production = _require_dict(document["production"], {
        "class_count", "function_count", "import_graph", "module_count",
        "modules", "physical_line_count", "top_level_function_count",
    }, "production")
    raw_modules = _require_list(production["modules"], "production.modules")
    expected_module_count = _require_int(
        production["module_count"], "production.module_count", 1
    )
    expected_function_count = _require_int(
        production["function_count"], "production.function_count"
    )
    expected_top_level_count = _require_int(
        production["top_level_function_count"],
        "production.top_level_function_count",
    )
    expected_class_count = _require_int(
        production["class_count"], "production.class_count"
    )
    expected_line_count = _require_int(
        production["physical_line_count"], "production.physical_line_count"
    )
    module_names: list[str] = []
    module_paths: list[str] = []
    function_count = 0
    top_level_function_count = 0
    class_count = 0
    line_count = 0
    direct_dependencies: dict[str, list[str]] = {}
    for index, raw_module in enumerate(raw_modules):
        context = f"production.modules[{index}]"
        module = _require_dict(raw_module, {
            "class_count", "classes", "direct_dependencies", "function_count",
            "functions", "module", "non_blank_lines", "path",
            "physical_lines", "top_level_function_count",
        }, context)
        name = _require_string(module["module"], f"{context}.module")
        path = _validate_path(module["path"], f"{context}.path")
        physical = _require_int(module["physical_lines"], f"{context}.physical_lines")
        non_blank = _require_int(module["non_blank_lines"], f"{context}.non_blank_lines")
        if non_blank > physical:
            raise ArchitectureInventoryError(f"{context} line counts are inconsistent")
        dependencies = _validate_sorted_strings(
            module["direct_dependencies"], f"{context}.direct_dependencies"
        )
        functions = _require_list(module["functions"], f"{context}.functions")
        for def_index, definition in enumerate(functions):
            _validate_definition(
                definition, f"{context}.functions[{def_index}]", function=True
            )
        classes = _require_list(module["classes"], f"{context}.classes")
        for def_index, definition in enumerate(classes):
            _validate_definition(
                definition, f"{context}.classes[{def_index}]", function=False
            )
        observed_top_level = sum(
            definition["scope"] == "top_level" for definition in functions
        )
        observed_function_count = _require_int(
            module["function_count"], f"{context}.function_count"
        )
        observed_top_level_count = _require_int(
            module["top_level_function_count"],
            f"{context}.top_level_function_count",
        )
        observed_class_count = _require_int(
            module["class_count"], f"{context}.class_count"
        )
        if observed_function_count != len(functions):
            raise ArchitectureInventoryError(
                f"{context}.function_count is inconsistent"
            )
        if observed_top_level_count != observed_top_level:
            raise ArchitectureInventoryError(
                f"{context}.top_level_function_count is inconsistent"
            )
        if observed_class_count != len(classes):
            raise ArchitectureInventoryError(
                f"{context}.class_count is inconsistent"
            )
        module_names.append(name)
        module_paths.append(path)
        direct_dependencies[name] = dependencies
        function_count += len(functions)
        top_level_function_count += observed_top_level
        class_count += len(classes)
        line_count += physical
    if module_names != sorted(set(module_names)):
        raise ArchitectureInventoryError("production.modules must be sorted and unique")
    if set(module_paths) != set(grouped_paths["production"]):
        raise ArchitectureInventoryError("production module/source paths differ")
    if expected_module_count != len(raw_modules):
        raise ArchitectureInventoryError("production.module_count is inconsistent")
    if expected_function_count != function_count:
        raise ArchitectureInventoryError("production.function_count is inconsistent")
    if expected_top_level_count != top_level_function_count:
        raise ArchitectureInventoryError(
            "production.top_level_function_count is inconsistent"
        )
    if expected_class_count != class_count:
        raise ArchitectureInventoryError("production.class_count is inconsistent")
    if expected_line_count != line_count:
        raise ArchitectureInventoryError("production.physical_line_count is inconsistent")
    known_modules = set(module_names)
    for module, dependencies in direct_dependencies.items():
        if not set(dependencies) <= known_modules:
            raise ArchitectureInventoryError(
                f"production module {module} has an unknown dependency"
            )

    graph = _require_dict(production["import_graph"], {
        "cyclic_components", "edge_count", "nodes",
        "strongly_connected_components",
    }, "production.import_graph")
    nodes = _require_list(graph["nodes"], "production.import_graph.nodes")
    observed_graph: dict[str, set[str]] = {}
    for index, raw_node in enumerate(nodes):
        context = f"production.import_graph.nodes[{index}]"
        node = _require_dict(raw_node, {"dependencies", "module", "path"}, context)
        name = _require_string(node["module"], f"{context}.module")
        path = _validate_path(node["path"], f"{context}.path")
        dependencies = _validate_sorted_strings(
            node["dependencies"], f"{context}.dependencies"
        )
        if name in observed_graph or name not in known_modules:
            raise ArchitectureInventoryError(f"{context}.module is invalid")
        expected_path = module_paths[module_names.index(name)]
        if path != expected_path or dependencies != direct_dependencies[name]:
            raise ArchitectureInventoryError(f"{context} differs from module data")
        observed_graph[name] = set(dependencies)
    if list(observed_graph) != sorted(known_modules):
        raise ArchitectureInventoryError("production.import_graph.nodes is incomplete")
    edge_count = _require_int(
        graph["edge_count"], "production.import_graph.edge_count"
    )
    if edge_count != sum(len(items) for items in observed_graph.values()):
        raise ArchitectureInventoryError("production.import_graph.edge_count is inconsistent")
    expected_components = _strongly_connected_components(observed_graph)
    if graph["strongly_connected_components"] != expected_components:
        raise ArchitectureInventoryError(
            "production.import_graph strongly connected components differ"
        )
    expected_cyclic = [
        component
        for component in expected_components
        if len(component) > 1 or component[0] in observed_graph[component[0]]
    ]
    if graph["cyclic_components"] != expected_cyclic:
        raise ArchitectureInventoryError(
            "production.import_graph cyclic components differ"
        )

    facade = _require_dict(document["rag_facade"], {
        "explicit_binding_count", "explicit_bindings", "import_star",
        "imported_bindings", "initializer_bindings", "local_definitions",
        "path",
    }, "rag_facade")
    if facade["path"] != "rag.py" or "rag.py" not in grouped_paths["production"]:
        raise ArchitectureInventoryError("rag_facade.path must be tracked rag.py")
    bindings = _validate_sorted_strings(
        facade["explicit_bindings"], "rag_facade.explicit_bindings"
    )
    binding_count = _require_int(
        facade["explicit_binding_count"], "rag_facade.explicit_binding_count"
    )
    if binding_count != len(bindings):
        raise ArchitectureInventoryError("rag_facade.explicit_binding_count is inconsistent")
    initializer_bindings = _require_list(
        facade["initializer_bindings"], "rag_facade.initializer_bindings"
    )
    initializer_names: list[str] = []
    for index, raw_binding in enumerate(initializer_bindings):
        context = f"rag_facade.initializer_bindings[{index}]"
        binding = _require_dict(
            raw_binding, {"initializer", "name"}, context
        )
        name = _require_string(binding["name"], f"{context}.name")
        _require_string(binding["initializer"], f"{context}.initializer")
        if name not in bindings:
            raise ArchitectureInventoryError(f"{context}.name is not a binding")
        initializer_names.append(name)
    if initializer_names != sorted(set(initializer_names)):
        raise ArchitectureInventoryError(
            "rag_facade.initializer_bindings is not sorted/unique"
        )
    import_star = _require_dict(
        facade["import_star"], {"names", "uses_explicit_all"},
        "rag_facade.import_star",
    )
    uses_all = _require_bool(
        import_star["uses_explicit_all"], "rag_facade.import_star.uses_explicit_all"
    )
    star_names = _validate_sorted_strings(
        import_star["names"], "rag_facade.import_star.names"
    )
    if not uses_all and star_names != [name for name in bindings if not name.startswith("_")]:
        raise ArchitectureInventoryError("rag_facade import-star surface is inconsistent")
    imported = _require_list(facade["imported_bindings"], "rag_facade.imported_bindings")
    for index, raw_import in enumerate(imported):
        _validate_import_record(raw_import, f"rag_facade.imported_bindings[{index}]")
    local_definitions = _require_list(
        facade["local_definitions"], "rag_facade.local_definitions"
    )
    local_keys: list[tuple[int, str, str]] = []
    for index, definition in enumerate(local_definitions):
        context = f"rag_facade.local_definitions[{index}]"
        record = _require_dict(
            definition, {"kind", "name", "span"}, context
        )
        if record["kind"] not in {"function", "async_function", "class"}:
            raise ArchitectureInventoryError(f"{context}.kind is invalid")
        name = _require_string(record["name"], f"{context}.name")
        if name not in bindings:
            raise ArchitectureInventoryError(f"{context}.name is not a binding")
        span = _require_list(record["span"], f"{context}.span")
        if len(span) != 2:
            raise ArchitectureInventoryError(f"{context}.span must have two lines")
        first = _require_int(span[0], f"{context}.span[0]", 1)
        _require_int(span[1], f"{context}.span[1]", first)
        local_keys.append((first, name, record["kind"]))
    if local_keys != sorted(local_keys):
        raise ArchitectureInventoryError(
            "rag_facade.local_definitions is not source-sorted"
        )

    consumers = _require_dict(
        document["consumers"], {"production", "tests"}, "consumers"
    )
    production_consumers = _require_list(
        consumers["production"], "consumers.production"
    )
    consumer_modules: list[str] = []
    for index, consumer in enumerate(production_consumers):
        _validate_consumer(
            consumer, f"consumers.production[{index}]", tracked_paths,
            known_modules,
        )
        consumer_modules.append(consumer["module"])
    if consumer_modules != sorted(set(consumer_modules)) or "rag" in consumer_modules:
        raise ArchitectureInventoryError("production consumers are not sorted/valid")

    tests = _require_dict(consumers["tests"], {
        "importing_module_count", "modules", "monkeypatch_seams",
        "private_references",
    }, "consumers.tests")
    test_modules = _require_list(tests["modules"], "consumers.tests.modules")
    observed_test_modules: list[str] = []
    for index, raw_consumer in enumerate(test_modules):
        context = f"consumers.tests.modules[{index}]"
        consumer = _require_dict(
            raw_consumer, {"imports", "module", "path"}, context
        )
        _validate_rag_imports(consumer["imports"], f"{context}.imports")
        _require_string(consumer["module"], f"{context}.module")
        path = _validate_path(consumer["path"], f"{context}.path")
        if path not in grouped_paths["tests"]:
            raise ArchitectureInventoryError(
                f"consumers.tests.modules[{index}] is not a test source"
            )
        observed_test_modules.append(consumer["module"])
    if observed_test_modules != sorted(set(observed_test_modules)):
        raise ArchitectureInventoryError("test consumers are not sorted/unique")
    importing_module_count = _require_int(
        tests["importing_module_count"],
        "consumers.tests.importing_module_count",
    )
    if importing_module_count != len(test_modules):
        raise ArchitectureInventoryError(
            "consumers.tests.importing_module_count is inconsistent"
        )
    private = _require_list(
        tests["private_references"], "consumers.tests.private_references"
    )
    private_names: list[str] = []
    for index, reference in enumerate(private):
        _validate_reference_group(
            reference, f"consumers.tests.private_references[{index}]", tracked_paths
        )
        name = reference["name"]
        if not name.startswith("_"):
            raise ArchitectureInventoryError(
                f"consumers.tests.private_references[{index}] is not private"
            )
        private_names.append(name)
    if private_names != sorted(set(private_names)):
        raise ArchitectureInventoryError("private references are not sorted/unique")

    patches = _require_dict(tests["monkeypatch_seams"], {
        "dynamic_sites", "nested", "top_level",
    }, "consumers.tests.monkeypatch_seams")
    tracked_test_paths = set(grouped_paths["tests"])
    dynamic = _require_list(
        patches["dynamic_sites"], "consumers.tests.monkeypatch_seams.dynamic_sites"
    )
    for index, site in enumerate(dynamic):
        _validate_patch_occurrence(
            site,
            f"consumers.tests.monkeypatch_seams.dynamic_sites[{index}]",
            tracked_test_paths,
        )
    all_targets: set[str] = set()
    for category in ("top_level", "nested"):
        entries = _require_list(
            patches[category], f"consumers.tests.monkeypatch_seams.{category}"
        )
        targets: list[str] = []
        for index, raw_entry in enumerate(entries):
            context = f"consumers.tests.monkeypatch_seams.{category}[{index}]"
            entry = _require_dict(
                raw_entry,
                {
                    "locations_sha256", "occurrence_count", "operations",
                    "sources", "target",
                },
                context,
            )
            target = _require_string(entry["target"], f"{context}.target")
            if ("." in target) != (category == "nested"):
                raise ArchitectureInventoryError(f"{context}.target depth is inconsistent")
            if target.startswith(".") or target.endswith(".") or ".." in target:
                raise ArchitectureInventoryError(f"{context}.target is invalid")
            operations = _validate_sorted_strings(
                entry["operations"], f"{context}.operations"
            )
            if not operations or not set(operations) <= {
                "setattr", "delattr", "patch", "patch.object",
            }:
                raise ArchitectureInventoryError(f"{context}.operations is invalid")
            _require_int(
                entry["occurrence_count"], f"{context}.occurrence_count", 1
            )
            _validate_sha256(
                entry["locations_sha256"], f"{context}.locations_sha256"
            )
            sources = _validate_sorted_strings(
                entry["sources"], f"{context}.sources"
            )
            if not sources:
                raise ArchitectureInventoryError(f"{context}.sources cannot be empty")
            for source_index, raw_source in enumerate(sources):
                source = _validate_path(
                    raw_source, f"{context}.sources[{source_index}]"
                )
                if source not in tracked_test_paths:
                    raise ArchitectureInventoryError(
                        f"{context}.sources[{source_index}] is not a tracked test"
                    )
            targets.append(target)
            if target in all_targets:
                raise ArchitectureInventoryError("monkeypatch target is duplicated")
            all_targets.add(target)
        if targets != sorted(targets):
            raise ArchitectureInventoryError(
                f"consumers.tests.monkeypatch_seams.{category} is not sorted"
            )


def inventory_bytes(value: object) -> bytes:
    """Return canonical, newline-terminated UTF-8 inventory bytes."""
    validate_inventory(value)
    return _canonical_json_bytes(value) + b"\n"


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArchitectureInventoryError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_inventory(path: Path) -> dict[str, object]:
    """Load one duplicate-safe, finite, schema-valid inventory."""
    try:
        payload = path.read_text(encoding="utf-8")
        value = json.loads(
            payload,
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ArchitectureInventoryError(
                    f"non-standard JSON number is forbidden: {token}"
                )
            ),
        )
    except ArchitectureInventoryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchitectureInventoryError(
            f"Cannot read architecture inventory {path.name}: {exc}"
        ) from exc
    validate_inventory(value)
    return value


def _resolved_baseline(root: Path, baseline: Path) -> Path:
    try:
        resolved_root = root.resolve(strict=True)
        candidate = baseline if baseline.is_absolute() else resolved_root / baseline
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ArchitectureInventoryError(
            "Baseline path must remain inside the repository"
        ) from exc
    if resolved.suffix.lower() != ".json":
        raise ArchitectureInventoryError("Baseline path must end in .json")
    return resolved


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def refresh_baseline(
    root: Path = PROJECT_ROOT, baseline: Path = DEFAULT_BASELINE,
) -> dict[str, object]:
    """Atomically replace *baseline* with the current canonical inventory."""
    value = build_inventory(root)
    path = _resolved_baseline(root, baseline)
    _atomic_write(path, inventory_bytes(value))
    return value


def check_baseline(
    root: Path = PROJECT_ROOT, baseline: Path = DEFAULT_BASELINE,
) -> dict[str, object]:
    """Validate *baseline* and require exact current canonical bytes."""
    path = _resolved_baseline(root, baseline)
    expected = inventory_bytes(load_inventory(path))
    try:
        observed_bytes = path.read_bytes()
    except OSError as exc:
        raise ArchitectureInventoryError(
            f"Cannot read architecture inventory {path.name}: {exc}"
        ) from exc
    # Git may materialize the single terminating LF as CRLF on Windows when a
    # checkout uses text=auto. The committed JSON payload remains identical;
    # no other whitespace or byte-level variation is accepted.
    normalized_observed = observed_bytes.replace(b"\r\n", b"\n")
    if normalized_observed != expected:
        raise ArchitectureInventoryError(
            "Architecture inventory baseline is not canonical; run with --refresh"
        )
    current = build_inventory(root)
    if inventory_bytes(current) != expected:
        raise ArchitectureInventoryError(
            "Architecture inventory differs from the committed baseline; "
            "review the change and run with --refresh"
        )
    return current


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="Git worktree root (defaults to this repository)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="repository-relative baseline JSON path",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--refresh",
        action="store_true",
        help="atomically replace the reviewed baseline",
    )
    modes.add_argument(
        "--report",
        action="store_true",
        help="write the current canonical JSON report to stdout",
    )
    return parser


def _summary(value: Mapping[str, object]) -> str:
    production = value["production"]
    facade = value["rag_facade"]
    tests = value["consumers"]["tests"]
    patches = tests["monkeypatch_seams"]
    return (
        f"{value['tracked_sources']['count']} tracked Python sources, "
        f"{production['module_count']} production modules, "
        f"{production['function_count']} functions "
        f"({production['top_level_function_count']} top-level), "
        f"{facade['explicit_binding_count']} rag bindings, "
        f"{len(patches['top_level'])} top-level and "
        f"{len(patches['nested'])} nested literal patch seams"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.report:
            value = build_inventory(args.root)
            sys.stdout.write(inventory_bytes(value).decode("utf-8"))
            return 0
        if args.refresh:
            value = refresh_baseline(args.root, args.baseline)
            action = "refreshed"
        else:
            value = check_baseline(args.root, args.baseline)
            action = "passed"
    except (ArchitectureInventoryError, OSError) as exc:
        print(f"Architecture inventory failed: {exc}", file=sys.stderr)
        return 1
    print(f"Architecture inventory {action}: {_summary(value)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
