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
import re
import subprocess
import sys
import tempfile
import textwrap
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = Path("architecture-inventory.json")
SCHEMA_VERSION = 2
ANALYSIS_MODEL = "tracked-python-ast-v2"
INTERPRETER_NORMALIZATION = "cpython-3.12-3.14-semantic-v1"
_GROUP_NAMES = ("production", "tests", "tools", "other")
_INTERPRETER_VARIANT_NAMES = (
    "__annotate__",
    "__annotations__",
    "__conditional_annotations__",
)

_RUNTIME_PROBE = r'''
import hashlib
import importlib
import inspect
import json
import os
import pickle
from pathlib import Path
import re
import socket
import ssl  # Import before replacing socket.socket so SSL class creation is complete.
import subprocess as probe_subprocess
import sys
import types
import typing

root = sys.argv[1]
sys.path.insert(0, root)
sys.dont_write_bytecode = True

def fresh_source_code(loader, fullname):
    source_path = loader.get_filename(fullname)
    source_bytes = loader.get_data(source_path)
    return loader.source_to_code(source_bytes, source_path)

importlib.machinery.SourceFileLoader.get_code = fresh_source_code

def denied_home(*_args, **_kwargs):
    raise RuntimeError("home lookup is disabled in the architecture probe")

def denied_expanduser(path):
    rendered = os.fspath(path)
    if rendered == "~" or rendered.startswith(("~/", "~\\")):
        raise RuntimeError("tilde expansion is disabled in the architecture probe")
    return rendered

class DeniedSocket(socket.socket):
    def __new__(cls, *_args, **_kwargs):
        raise RuntimeError("network access is disabled in the architecture probe")

def denied_network(*_args, **_kwargs):
    raise RuntimeError("network access is disabled in the architecture probe")

Path.home = classmethod(denied_home)
os.path.expanduser = denied_expanduser
socket.socket = DeniedSocket
socket.socketpair = denied_network
socket.create_connection = denied_network
socket.getaddrinfo = denied_network
probe_subprocess.Popen = denied_network

import rag

variant_names = {"__annotate__", "__annotations__", "__conditional_annotations__"}

def digest(value):
    payload = json.dumps(
        value, allow_nan=False, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def value_identity(value):
    if value is inspect.Signature.empty or value is inspect.Parameter.empty:
        return {"kind": "empty"}
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return {
            "kind": f"{type(value).__module__}.{type(value).__qualname__}",
            "value_sha256": hashlib.sha256(repr(value).encode("utf-8")).hexdigest(),
        }
    if isinstance(value, (list, tuple)):
        return {
            "items": [value_identity(item) for item in value],
            "kind": f"sequence:{type(value).__name__}",
        }
    if isinstance(value, (set, frozenset)):
        items = [value_identity(item) for item in value]
        return {
            "items": sorted(items, key=digest),
            "kind": f"set:{type(value).__name__}",
        }
    if isinstance(value, dict):
        items = [
            [value_identity(key), value_identity(item)]
            for key, item in value.items()
        ]
        return {"items": sorted(items, key=digest), "kind": "mapping"}
    if isinstance(value, os.PathLike):
        return {"kind": f"{type(value).__module__}.{type(value).__qualname__}"}
    origin = typing.get_origin(value)
    if origin is not None:
        args = []
        for item in typing.get_args(value):
            if isinstance(item, (list, tuple)):
                args.append({
                    "items": [value_identity(nested) for nested in item],
                    "kind": "callable_parameters",
                })
            else:
                args.append(value_identity(item))
        if origin in {typing.Union, types.UnionType}:
            return {"args": sorted(args, key=digest), "kind": "union"}
        return {
            "args": args,
            "kind": "typing",
            "origin": value_identity(origin),
        }
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return {"kind": "identity", "module": module, "qualname": qualname}
    rendered = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", repr(value))
    return {
        "kind": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    }

def project_owned(value):
    try:
        source = inspect.getsourcefile(value)
        return Path(source).resolve().is_relative_to(Path(root).resolve())
    except (OSError, TypeError, ValueError):
        return False

def callable_record(name, value):
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if project_owned(value):
        try:
            signature = inspect.signature(value)
            signature_shape = {
                "parameters": [
                    {
                        "annotation": value_identity(parameter.annotation),
                        "default": value_identity(parameter.default),
                        "kind": parameter.kind.name,
                        "name": parameter.name,
                    }
                    for parameter in signature.parameters.values()
                ],
                "return": value_identity(signature.return_annotation),
            }
            signature_status = "ok"
        except (TypeError, ValueError) as exc:
            signature_shape = {"error": type(exc).__name__}
            signature_status = "unsupported"
        try:
            hints = {
                key: value_identity(item)
                for key, item in sorted(typing.get_type_hints(value).items())
            }
            hints_status = "ok"
        except Exception as exc:
            hints = {"error": type(exc).__name__}
            hints_status = "unresolved"
    else:
        signature_shape = {
            "kind": "interpreter_owned", "module": module, "qualname": qualname,
        }
        signature_status = "interpreter_owned"
        hints = signature_shape
        hints_status = "interpreter_owned"
    try:
        pickle_status = (
            "same" if pickle.loads(pickle.dumps(value)) is value else "different"
        )
    except Exception:
        pickle_status = "unsupported"
    return {
        "kind": "class" if inspect.isclass(value) else "function",
        "module": module,
        "name": name,
        "pickle_identity": pickle_status,
        "qualname": qualname,
        "runtime_signature_sha256": digest(signature_shape),
        "runtime_signature_status": signature_status,
        "type_hints_sha256": digest(hints),
        "type_hints_status": hints_status,
    }

behavior = {}
dynamic_name = "_architecture_inventory_dynamic_probe"
sentinel = object()
setattr(rag, dynamic_name, sentinel)
behavior["dynamic_add_read"] = (
    getattr(rag, dynamic_name) is sentinel
    and vars(rag)[dynamic_name] is sentinel
    and dynamic_name in dir(rag)
)
delattr(rag, dynamic_name)
behavior["dynamic_delete"] = (
    not hasattr(rag, dynamic_name)
    and dynamic_name not in vars(rag)
    and dynamic_name not in dir(rag)
)

implementation_globals = rag._call_llm_result.__globals__
original_runtime = rag._llm_runtime
rag._llm_runtime = sentinel
behavior["existing_assignment_visible"] = (
    implementation_globals.get("_llm_runtime") is sentinel
)
rag._llm_runtime = original_runtime
behavior["existing_assignment_restored"] = (
    rag._llm_runtime is original_runtime
    and implementation_globals.get("_llm_runtime") is original_runtime
)
del rag._llm_runtime
behavior["existing_deletion_visible"] = (
    "_llm_runtime" not in vars(rag)
    and "_llm_runtime" not in implementation_globals
)
rag._llm_runtime = original_runtime
behavior["existing_deletion_restored"] = (
    rag._llm_runtime is original_runtime
    and implementation_globals.get("_llm_runtime") is original_runtime
)

original_nested = rag.log.disabled
rag.log.disabled = not original_nested
behavior["nested_assignment_visible"] = rag.log.disabled is (not original_nested)
rag.log.disabled = original_nested
behavior["nested_assignment_restored"] = rag.log.disabled is original_nested

setattr(rag, dynamic_name, sentinel)
rag._llm_runtime = sentinel
before_reload = rag
reloaded = importlib.reload(rag)
behavior["reload_same_module"] = reloaded is before_reload
behavior["reload_preserves_dynamic"] = getattr(rag, dynamic_name, None) is sentinel
behavior["reload_reexecutes_existing_binding"] = rag._llm_runtime is not sentinel
delattr(rag, dynamic_name)

namespace = {}
exec("from rag import *", namespace)
import_star_names = sorted(name for name in namespace if name != "__builtins__")
vars_names = sorted(name for name in vars(rag) if name not in variant_names)
dir_names = sorted(name for name in dir(rag) if name not in variant_names)
callables = [
    callable_record(name, value)
    for name, value in sorted(vars(rag).items())
    if name not in variant_names
    and (inspect.isfunction(value) or inspect.isclass(value))
]

result = {
    "behavior": behavior,
    "callables": callables,
    "dir_names": dir_names,
    "import_star_names": import_star_names,
    "interpreter_variant_exclusions": sorted(variant_names),
    "logger_name": rag.log.name,
    "module_type": f"{type(rag).__module__}.{type(rag).__qualname__}",
    "vars_names": vars_names,
}
sys.stdout.write(json.dumps(
    result, allow_nan=False, ensure_ascii=False,
    separators=(",", ":"), sort_keys=True,
))
'''

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
        tree = ast.parse(
            source, filename=relative.as_posix(), type_comments=True
        )
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


def _type_checking_guard(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    chain = _attribute_chain(node)
    return chain == ("typing", ["TYPE_CHECKING"])


class _ImportEdgeVisitor(ast.NodeVisitor):
    def __init__(
        self, current: str, path: Path, known: set[str],
    ) -> None:
        self.current = current
        self.path = path
        self.known = known
        self.context = "runtime"
        self.edges: dict[str, set[str]] = defaultdict(set)

    def _with_context(self, context: str, nodes: Iterable[ast.AST]) -> None:
        previous = self.context
        self.context = context
        for node in nodes:
            self.visit(node)
        self.context = previous

    def _add(self, targets: Iterable[str], *extra: str) -> None:
        for target in targets:
            self.edges[target].update((self.context, *extra))

    def visit_Import(self, node: ast.Import) -> None:
        self._add(_import_targets(self.current, self.path, node, self.known))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._add(_import_targets(self.current, self.path, node, self.known))

    def visit_Call(self, node: ast.Call) -> None:
        dynamic_name: str | None = None
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            dynamic_name = node.args[0].value
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            dynamic_name = node.args[0].value
        if dynamic_name:
            self._add(
                _known_prefixes(dynamic_name, self.known),
                "dynamic_constant",
            )
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        if _type_checking_guard(node.test):
            self._with_context("type_only", node.body)
            self._with_context(self.context, node.orelse)
            return
        context = "type_only" if self.context == "type_only" else "conditional"
        self._with_context(context, node.body)
        self._with_context(context, node.orelse)

    def _visit_conditional_body(self, node: ast.AST) -> None:
        context = "type_only" if self.context == "type_only" else "conditional"
        self._with_context(context, ast.iter_child_nodes(node))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_conditional_body(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_conditional_body(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_conditional_body(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_conditional_body(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_conditional_body(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_conditional_body(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_conditional_body(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_conditional_body(node)

    def visit_Match(self, node: ast.Match) -> None:
        self._visit_conditional_body(node)


def _module_import_edges(
    module: str, path: Path, tree: ast.Module, known: set[str],
) -> dict[str, set[str]]:
    visitor = _ImportEdgeVisitor(module, path, known)
    visitor.visit(tree)
    return visitor.edges


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


def _canonical_ast_value(value: object) -> object:
    if isinstance(value, ast.AST):
        return {
            "fields": [
                [field, _canonical_ast_value(getattr(value, field))]
                for field in value._fields
            ],
            "node": type(value).__name__,
        }
    if isinstance(value, list):
        return [_canonical_ast_value(item) for item in value]
    if value is Ellipsis:
        return {"constant": "ellipsis"}
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, complex):
        return {"complex": [value.real, value.imag]}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ArchitectureInventoryError(
        f"unsupported AST scalar in signature: {type(value).__name__}"
    )


def _ast_identity(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    return _canonical_sha256(_canonical_ast_value(node))


def _text_identity(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _signature_inventory(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, object]:
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    positional_defaults: list[ast.AST | None] = [
        *([None] * (len(positional) - len(arguments.defaults))),
        *arguments.defaults,
    ]
    parameters: list[dict[str, object]] = []

    def add(argument: ast.arg, kind: str, default: ast.AST | None) -> None:
        parameters.append({
            "annotation_sha256": _ast_identity(argument.annotation),
            "default_sha256": _ast_identity(default),
            "has_default": default is not None,
            "kind": kind,
            "name": argument.arg,
        })

    for index, argument in enumerate(arguments.posonlyargs):
        add(argument, "positional_only", positional_defaults[index])
    offset = len(arguments.posonlyargs)
    for index, argument in enumerate(arguments.args):
        add(
            argument,
            "positional_or_keyword",
            positional_defaults[offset + index],
        )
    if arguments.vararg is not None:
        add(arguments.vararg, "var_positional", None)
    for argument, default in zip(
        arguments.kwonlyargs, arguments.kw_defaults, strict=True
    ):
        add(argument, "keyword_only", default)
    if arguments.kwarg is not None:
        add(arguments.kwarg, "var_keyword", None)
    return {
        "parameters": parameters,
        "return_annotation_sha256": _ast_identity(node.returns),
        "type_comment_sha256": _text_identity(node.type_comment),
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
            "kind": kind,
            "name": qualified,
            "scope": self._scope_kind(),
            "signature": _signature_inventory(node),
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
            if node.value is not None:
                self._record(node.target, node.lineno)
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
                if isinstance(statement, ast.AnnAssign) and statement.value is None:
                    continue
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
    scope_map = _ScopeMap(tree)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def under_main_guard(node: ast.AST) -> bool:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, ast.If) and is_main_guard(current.test):
                return True
            current = parents.get(current)
        return False

    for node, scope in scope_map.node_scope.items():
        if scope is not tree or under_main_guard(node):
            continue
        if isinstance(node, ast.NamedExpr):
            for name in _target_names(node.target):
                add(name, "named_expression", node.lineno)
        elif isinstance(node, ast.Delete):
            raise ArchitectureInventoryError(
                "rag.py module-scope del requires explicit namespace analysis"
            )
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
    scope_map = _ScopeMap(tree)
    candidates: list[tuple[ast.AST, ast.AST | None]] = []
    for node, scope in scope_map.node_scope.items():
        if scope is not tree:
            continue
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(item, ast.Name) and item.id == "__all__"
            for item in node.targets
        ):
            target, value = node, node.value
        elif (
            isinstance(node, (ast.AnnAssign, ast.AugAssign))
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            target = node
            value = node.value if isinstance(node, ast.AnnAssign) else None
        elif (
            isinstance(node, ast.NamedExpr)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            target, value = node, node.value
        elif isinstance(node, ast.Delete) and any(
            isinstance(item, ast.Name) and item.id == "__all__"
            for item in node.targets
        ):
            target = node
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            chain = _attribute_chain(node.func)
            if chain is not None and chain[0] == "__all__":
                target = node
        if target is not None:
            candidates.append((target, value))
    if not candidates:
        return False, []
    target, value = candidates[0]
    if (
        len(candidates) != 1
        or target not in tree.body
        or not isinstance(target, (ast.Assign, ast.AnnAssign))
        or value is None
    ):
        raise ArchitectureInventoryError(
            "rag.__all__ must be one unconditional literal assignment"
        )
    try:
        raw = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ArchitectureInventoryError(
            "rag.__all__ must be one unconditional literal string list or tuple"
        ) from exc
    if (
        not isinstance(raw, (list, tuple))
        or not all(isinstance(item, str) and item for item in raw)
    ):
        raise ArchitectureInventoryError(
            "rag.__all__ must be one unconditional literal string list or tuple"
        )
    values = list(raw)
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


class _ScopeMap(ast.NodeVisitor):
    """Map syntax nodes to lexical scopes without leaking shadowed aliases."""

    _COMPREHENSIONS = (
        ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    )

    def __init__(self, tree: ast.Module) -> None:
        self.current: ast.AST = tree
        self.node_scope: dict[ast.AST, ast.AST] = {tree: tree}
        self.parent_scope: dict[ast.AST, ast.AST | None] = {tree: None}
        self.scopes: list[ast.AST] = [tree]
        self.visit(tree)

    def visit(self, node: ast.AST) -> object:
        self.node_scope.setdefault(node, self.current)
        return super().visit(node)

    def _visit_arguments_in_outer_scope(self, node: ast.arguments) -> None:
        for argument in [
            *node.posonlyargs, *node.args, *node.kwonlyargs,
            *([node.vararg] if node.vararg else []),
            *([node.kwarg] if node.kwarg else []),
        ]:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        for default in [*node.defaults, *node.kw_defaults]:
            if default is not None:
                self.visit(default)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments_in_outer_scope(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        outer = self.current
        self.parent_scope[node] = outer
        self.scopes.append(node)
        self.current = node
        for statement in node.body:
            self.visit(statement)
        self.current = outer

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments_in_outer_scope(node.args)
        outer = self.current
        self.parent_scope[node] = outer
        self.scopes.append(node)
        self.current = node
        self.visit(node.body)
        self.current = outer

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        outer = self.current
        self.parent_scope[node] = outer
        self.scopes.append(node)
        self.current = node
        for statement in node.body:
            self.visit(statement)
        self.current = outer

    def _visit_comprehension(self, node: ast.AST) -> None:
        outer = self.current
        self.parent_scope[node] = outer
        self.scopes.append(node)
        self.current = node
        self.generic_visit(node)
        self.current = outer

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node)


class _FacadeAnalysis:
    """Resolve direct, re-exported, propagated, and scope-safe rag aliases."""

    def __init__(
        self,
        tree: ast.Module,
        exposed_bindings: Mapping[str, set[str]],
    ) -> None:
        self.tree = tree
        self.exposed_bindings = exposed_bindings
        self.scope_map = _ScopeMap(tree)
        self.names: dict[ast.AST, set[str]] = {}
        self.prefixes: dict[
            ast.AST, dict[str, set[tuple[str, ...]]]
        ] = {}
        self._build()

    @staticmethod
    def _arguments(scope: ast.AST) -> list[ast.arg]:
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return []
        arguments = scope.args
        return [
            *arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
            *([arguments.vararg] if arguments.vararg else []),
            *([arguments.kwarg] if arguments.kwarg else []),
        ]

    def _scope_bindings(
        self, scope: ast.AST,
    ) -> tuple[set[str], set[str], set[str]]:
        bound = {argument.arg for argument in self._arguments(scope)}
        globals_: set[str] = set()
        nonlocals: set[str] = set()
        for node, owner in self.scope_map.node_scope.items():
            if owner is not scope:
                continue
            if isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                bound.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    bound.update(
                        alias.asname or alias.name.split(".", 1)[0]
                        for alias in node.names
                    )
                else:
                    bound.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name != "*"
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if self.scope_map.node_scope[node] is scope:
                    bound.add(node.name)
            elif isinstance(node, ast.Global):
                globals_.update(node.names)
            elif isinstance(node, ast.Nonlocal):
                nonlocals.update(node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
        bound.difference_update(globals_ | nonlocals)
        return bound, globals_, nonlocals

    def _lexical_parent(self, scope: ast.AST) -> ast.AST | None:
        parent = self.scope_map.parent_scope[scope]
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            while isinstance(parent, ast.ClassDef):
                parent = self.scope_map.parent_scope[parent]
        return parent

    def _add_imports(
        self,
        scope: ast.AST,
        names: set[str],
        prefixes: dict[str, set[tuple[str, ...]]],
    ) -> None:
        for node, owner in self.scope_map.node_scope.items():
            if owner is not scope:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "rag":
                        names.add(alias.asname or "rag")
                        continue
                    exported = self.exposed_bindings.get(alias.name, set())
                    if not exported:
                        continue
                    if alias.asname:
                        binding = alias.asname
                        module_prefix: tuple[str, ...] = ()
                    else:
                        parts = tuple(alias.name.split("."))
                        binding = parts[0]
                        module_prefix = parts[1:]
                    for facade_binding in exported:
                        prefixes[binding].add(
                            (*module_prefix, facade_binding)
                        )
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                exported = self.exposed_bindings.get(node.module or "", set())
                for alias in node.names:
                    if alias.name == "*":
                        names.update(exported)
                    elif alias.name in exported:
                        names.add(alias.asname or alias.name)

    @staticmethod
    def _assignment_pairs(node: ast.AST) -> list[tuple[ast.AST, ast.AST]]:
        if isinstance(node, ast.Assign):
            result: list[tuple[ast.AST, ast.AST]] = []
            for target in node.targets:
                if (
                    isinstance(target, (ast.Tuple, ast.List))
                    and isinstance(node.value, (ast.Tuple, ast.List))
                    and len(target.elts) == len(node.value.elts)
                ):
                    result.extend(zip(target.elts, node.value.elts))
                else:
                    result.append((target, node.value))
            return result
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            return [(node.target, node.value)]
        if isinstance(node, ast.NamedExpr):
            return [(node.target, node.value)]
        return []

    @staticmethod
    def _resolve_with(
        node: ast.AST,
        names: set[str],
        prefixes: Mapping[str, set[tuple[str, ...]]],
    ) -> list[str] | None:
        chain = _attribute_chain(node)
        if chain is None:
            return None
        root, attributes = chain
        if root in names:
            return attributes
        for prefix in sorted(
            prefixes.get(root, set()),
            key=lambda value: (-len(value), value),
        ):
            if tuple(attributes[:len(prefix)]) == prefix:
                return attributes[len(prefix):]
        return None

    def _build(self) -> None:
        for scope in self.scope_map.scopes:
            parent = self._lexical_parent(scope)
            names = set(self.names.get(parent, set()))
            prefixes = defaultdict(set, {
                name: set(values)
                for name, values in self.prefixes.get(parent, {}).items()
            })
            bound, globals_, _nonlocals = self._scope_bindings(scope)
            for name in bound:
                names.discard(name)
                prefixes.pop(name, None)
            if scope is not self.tree:
                for name in globals_:
                    names.discard(name)
                    prefixes.pop(name, None)
                    if name in self.names[self.tree]:
                        names.add(name)
                    if name in self.prefixes[self.tree]:
                        prefixes[name].update(self.prefixes[self.tree][name])
            self._add_imports(scope, names, prefixes)
            changed = True
            while changed:
                changed = False
                for node, owner in self.scope_map.node_scope.items():
                    if owner is not scope:
                        continue
                    for target, value in self._assignment_pairs(node):
                        if self._resolve_with(value, names, prefixes) != []:
                            continue
                        for name in _target_names(target):
                            if name not in names:
                                names.add(name)
                                changed = True
            self.names[scope] = names
            self.prefixes[scope] = dict(prefixes)

    def resolve(self, node: ast.AST) -> list[str] | None:
        scope = self.scope_map.node_scope.get(node, self.tree)
        return self._resolve_with(
            node, self.names.get(scope, set()), self.prefixes.get(scope, {})
        )

    def roots(self) -> list[str]:
        roots: set[str] = set()
        for scope in self.scope_map.scopes:
            roots.update(self.names.get(scope, set()))
            roots.update(
                ".".join((name, *prefix))
                for name, prefixes in self.prefixes.get(scope, {}).items()
                for prefix in prefixes
            )
        return sorted(roots)


def _facade_attribute_accesses(
    tree: ast.Module, resolver: _FacadeAnalysis, path: str,
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
        capability = resolver.resolve(node)
        if not capability:
            continue
        context = type(node.ctx).__name__.lower()
        references.append({
            "context": context,
            "kind": "attribute",
            "line": node.lineno,
            "name": capability[0],
            "path": ".".join(capability),
            "source": path,
        })
    return sorted(
        references,
        key=lambda item: (
            item["source"], item["line"], item["path"], item["context"]
        ),
    )


def _literal_facade_target(
    value: object,
    exposed_bindings: Mapping[str, set[str]],
) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("rag."):
        return value[4:]
    for module, bindings in sorted(exposed_bindings.items()):
        for binding in sorted(bindings):
            prefix = f"{module}.{binding}."
            if value.startswith(prefix):
                return value[len(prefix):]
    return None


def _patch_target(
    call: ast.Call,
    resolver: _FacadeAnalysis,
    exposed_bindings: Mapping[str, set[str]],
) -> tuple[str, str] | None:
    function_name = (
        call.func.id
        if isinstance(call.func, ast.Name)
        else call.func.attr
        if isinstance(call.func, ast.Attribute)
        else ""
    )
    if (
        function_name in {"setattr", "delattr"}
        and isinstance(call.func, ast.Attribute)
    ):
        if not call.args:
            return None
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            target = _literal_facade_target(
                first.value, exposed_bindings
            )
            return (function_name, target) if target else None
        capability = resolver.resolve(first)
        if capability is None:
            return None
        if len(call.args) < 2 or not isinstance(call.args[1], ast.Constant):
            return None
        attribute = call.args[1].value
        if not isinstance(attribute, str) or not attribute:
            return None
        return function_name, ".".join([*capability, attribute])

    if function_name == "patch":
        if call.args and isinstance(call.args[0], ast.Constant):
            target = _literal_facade_target(
                call.args[0].value, exposed_bindings
            )
            if target:
                return "patch", target
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
        capability = resolver.resolve(call.args[0])
        attribute = call.args[1]
        if (
            capability is None
            or not isinstance(attribute, ast.Constant)
            or not isinstance(attribute.value, str)
        ):
            return None
        return "patch.object", ".".join([*capability, attribute.value])
    return None


def _source_counts(records: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[str(record["source"])] += 1
    return [
        {"count": count, "path": path}
        for path, count in sorted(counts.items())
    ]


def _target_inventory(
    grouped: Mapping[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
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
            "source_counts": _source_counts(occurrences),
            "target": target,
        }
        (nested if "." in target else top_level).append(record)
    return {"nested": nested, "top_level": top_level}


def _facade_tree_variants(
    tree: ast.Module,
) -> list[tuple[ast.Module, int, str]]:
    """Return outer and explicitly rag-importing embedded Python trees.

    Some isolation tests execute module-sized programs stored in literal
    strings.  Those programs are an architectural consumer surface even
    though the outer test process does not import the facade.
    """
    variants = [(tree, 0, "source")]
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, str)
            or len(node.value) > 1024 * 1024
            or not re.search(r"(?m)^\s*(?:from\s+rag\s+import|import\s+rag\b)", node.value)
        ):
            continue
        try:
            embedded = ast.parse(textwrap.dedent(node.value), type_comments=True)
        except (SyntaxError, ValueError, MemoryError):
            continue
        _aliases, imports, _from_imports = _rag_aliases(embedded)
        if imports:
            variants.append((embedded, node.lineno - 1, "embedded_python"))
    return variants


def _patch_inventory(
    trees: Mapping[Path, ast.Module],
    exposed_bindings: Mapping[str, set[str]],
) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    dynamic: list[dict[str, object]] = []
    for relative in sorted(trees, key=lambda item: item.as_posix()):
        for tree, line_offset, _source_kind in _facade_tree_variants(
            trees[relative]
        ):
            resolver = _FacadeAnalysis(tree, exposed_bindings)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = _patch_target(node, resolver, exposed_bindings)
                if target is not None:
                    operation, dotted = target
                    record = {
                        "line": node.lineno + line_offset,
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
                if resolver.resolve(node.args[0]) is None:
                    continue
                dynamic.append({
                    "line": node.lineno + line_offset,
                    "operation": function_name,
                    "source": relative.as_posix(),
                })

    result: dict[str, object] = {
        "dynamic_sites": sorted(
            dynamic,
            key=lambda item: (item["source"], item["line"], item["operation"]),
        ),
    }
    result.update(_target_inventory(grouped))
    return result


def _direct_mutation_inventory(
    trees: Mapping[Path, ast.Module],
    exposed_bindings: Mapping[str, set[str]],
) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    dynamic: list[dict[str, object]] = []
    for relative in sorted(trees, key=lambda item: item.as_posix()):
        source = relative.as_posix()
        for tree, line_offset, _source_kind in _facade_tree_variants(
            trees[relative]
        ):
            resolver = _FacadeAnalysis(tree, exposed_bindings)
            for access in _facade_attribute_accesses(tree, resolver, source):
                if access["context"] not in {"store", "del"}:
                    continue
                operation = "assign" if access["context"] == "store" else "delete"
                grouped[str(access["path"])].append({
                    "line": int(access["line"]) + line_offset,
                    "operation": operation,
                    "source": source,
                })
            for node in ast.walk(tree):
                if (
                    not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Name)
                    or node.func.id not in {"setattr", "delattr"}
                    or not node.args
                ):
                    continue
                capability = resolver.resolve(node.args[0])
                if capability is None:
                    continue
                operation = node.func.id
                if (
                    len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                    and node.args[1].value
                ):
                    target = ".".join([*capability, node.args[1].value])
                    grouped[target].append({
                        "line": node.lineno + line_offset,
                        "operation": operation,
                        "source": source,
                    })
                else:
                    dynamic.append({
                        "line": node.lineno + line_offset,
                        "operation": operation,
                        "source": source,
                    })
    result: dict[str, object] = {
        "dynamic_sites": sorted(
            dynamic,
            key=lambda item: (item["source"], item["line"], item["operation"]),
        ),
    }
    result.update(_target_inventory(grouped))
    return result


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
            "source_counts": _source_counts(locations),
        })
    return result


def _consumer_inventory(
    module: str,
    path: Path,
    tree: ast.Module,
    exposed_bindings: Mapping[str, set[str]],
) -> dict[str, object] | None:
    _aliases, direct_imports, from_imports = _rag_aliases(tree)
    analysis = _FacadeAnalysis(tree, exposed_bindings)
    references = [
        reference
        for reference in _facade_attribute_accesses(
            tree, analysis, path.as_posix()
        )
        if reference["context"] == "load"
    ]
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
    if not direct_imports and not references:
        return None
    return {
        "direct_imports": direct_imports,
        "facade_roots": analysis.roots(),
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


def _facade_exports(
    modules: Mapping[str, Path],
    trees: Mapping[Path, ast.Module],
) -> dict[str, set[str]]:
    exports: dict[str, set[str]] = {module: set() for module in modules}
    changed = True
    while changed:
        changed = False
        for module, path in sorted(modules.items()):
            if module == "rag":
                continue
            analysis = _FacadeAnalysis(trees[path], exports)
            observed = analysis.names[trees[path]]
            if not observed <= exports[module]:
                exports[module].update(observed)
                changed = True
    return {
        module: bindings
        for module, bindings in sorted(exports.items())
        if bindings
    }


def _runtime_facade_contract(root: Path) -> dict[str, object]:
    allowed_environment = {
        "COMSPEC", "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR",
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in allowed_environment
    }
    with tempfile.TemporaryDirectory(prefix="rag-architecture-probe-") as temporary:
        isolated_root = Path(temporary)
        redirected = {
            "APPDATA": isolated_root / "profile" / "AppData" / "Roaming",
            "LOCALAPPDATA": isolated_root / "profile" / "AppData" / "Local",
            "RAG_LLM_CACHE_DIR": isolated_root / "rag-llm-cache",
            "RAG_MODEL_ARTIFACT_CACHE": isolated_root / "rag-model-cache",
            "RAG_PIPELINE_OUTPUT_ROOT": isolated_root / "output",
            "TEMP": isolated_root / "tmp",
            "TMP": isolated_root / "tmp",
            "TMPDIR": isolated_root / "tmp",
            "USERPROFILE": isolated_root / "profile",
            "XDG_CACHE_HOME": isolated_root / "xdg" / "cache",
            "XDG_CONFIG_HOME": isolated_root / "xdg" / "config",
            "XDG_DATA_HOME": isolated_root / "xdg" / "data",
            "XDG_STATE_HOME": isolated_root / "xdg" / "state",
        }
        for directory in redirected.values():
            directory.mkdir(parents=True, exist_ok=True)
        drive, tail = os.path.splitdrive(str(redirected["USERPROFILE"]))
        environment.update({
            **{name: str(path) for name, path in redirected.items()},
            "HOMEDRIVE": drive,
            "HOMEPATH": tail or os.sep,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        })
        stdout_path = isolated_root / "stdout.json"
        stderr_path = isolated_root / "stderr.log"
        try:
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                result = subprocess.run(
                    [sys.executable, "-I", "-c", _RUNTIME_PROBE, str(root)],
                    cwd=temporary,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=90,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ArchitectureInventoryError(
                "Isolated rag runtime probe timed out"
            ) from exc
        stdout_size = stdout_path.stat().st_size
        stderr_size = stderr_path.stat().st_size
        if stdout_size > 4 * 1024 * 1024 or stderr_size > 4 * 1024 * 1024:
            raise ArchitectureInventoryError(
                "Isolated rag runtime probe exceeded its output ceiling"
            )
        if result.returncode != 0:
            exit_kind = "signal" if result.returncode < 0 else "nonzero exit"
            raise ArchitectureInventoryError(
                f"Isolated rag runtime probe failed ({exit_kind})"
            )
        payload = stdout_path.read_bytes()
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ArchitectureInventoryError(
                    f"runtime probe emitted non-standard number: {token}"
                )
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArchitectureInventoryError(
            "Isolated rag runtime probe emitted invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ArchitectureInventoryError(
            "Isolated rag runtime probe did not emit an object"
        )
    return value


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

    architecture_groups = ("production", "tools", "other")
    architecture_paths = [
        path for group in architecture_groups for path in groups[group]
    ]
    modules: dict[str, Path] = {}
    for relative in architecture_paths:
        module = _module_name(relative)
        if module in modules:
            raise ArchitectureInventoryError(
                f"Duplicate architecture module identity: {module}"
            )
        modules[module] = relative
    if "rag" not in modules or modules["rag"].as_posix() != "rag.py":
        raise ArchitectureInventoryError("The tracked production rag.py facade is missing")

    known = set(modules)
    graph: dict[str, set[str]] = {module: set() for module in modules}
    edge_kinds: dict[str, dict[str, set[str]]] = {}
    module_records: list[dict[str, object]] = []
    for module, relative in sorted(modules.items()):
        tree = trees[relative]
        edges = _module_import_edges(module, relative, tree, known)
        edge_kinds[module] = edges
        graph[module].update(edges)
        functions, classes = _definitions(tree)
        source = sources[relative]
        module_records.append({
            "class_count": len(classes),
            "classes": classes,
            "function_count": len(functions),
            "functions": functions,
            "import_edges": [
                {"kinds": sorted(kinds), "target": target}
                for target, kinds in sorted(edges.items())
            ],
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
            "edges": [
                {"kinds": sorted(kinds), "target": target}
                for target, kinds in sorted(edge_kinds[module].items())
            ],
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

    exposed_bindings = _facade_exports(modules, trees)
    production_consumers: list[dict[str, object]] = []
    for module, relative in sorted(modules.items()):
        if module == "rag":
            continue
        consumer = _consumer_inventory(
            module, relative, trees[relative], exposed_bindings
        )
        if consumer is not None:
            production_consumers.append(consumer)

    test_consumers: list[dict[str, object]] = []
    private_references: list[dict[str, object]] = []
    test_trees = {path: trees[path] for path in groups["tests"]}
    for relative in sorted(groups["tests"], key=lambda item: item.as_posix()):
        _aliases, _imports, from_imports = _rag_aliases(trees[relative])
        consumer = _consumer_inventory(
            _module_name(relative), relative, trees[relative], exposed_bindings
        )
        analysis = _FacadeAnalysis(trees[relative], exposed_bindings)
        direct_references = [
            reference
            for reference in _facade_attribute_accesses(
                trees[relative], analysis, relative.as_posix()
            )
            if reference["context"] == "load"
        ]
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
        if consumer is not None:
            test_consumers.append({
                "facade_roots": consumer["facade_roots"],
                "module": consumer["module"],
                "path": consumer["path"],
            })

    patches = _patch_inventory(test_trees, exposed_bindings)
    direct_mutations = _direct_mutation_inventory(
        test_trees, exposed_bindings
    )

    inventory = {
        "analysis_model": ANALYSIS_MODEL,
        "consumers": {
            "production": production_consumers,
            "tests": {
                "using_module_count": len(test_consumers),
                "direct_mutations": direct_mutations,
                "modules": test_consumers,
                "monkeypatch_seams": patches,
                "private_read_references": _reference_groups(
                    private_references
                ),
            },
        },
        "source_architecture": {
            "class_count": sum(len(item["classes"]) for item in module_records),
            "function_count": sum(len(item["functions"]) for item in module_records),
            "import_graph": {
                "cyclic_components": cyclic,
                "edge_policy": "runtime+conditional+type-only+constant-dynamic",
                "edge_count": sum(len(dependencies) for dependencies in graph.values()),
                "nodes": graph_nodes,
                "strongly_connected_components": components,
            },
            "module_count": len(module_records),
            "modules": module_records,
            "physical_line_count": sum(item["physical_lines"] for item in module_records),
            "source_groups": list(architecture_groups),
            "top_level_function_count": sum(
                item["top_level_function_count"] for item in module_records
            ),
        },
        "interpreter_normalization": INTERPRETER_NORMALIZATION,
        "rag_runtime_contract": _runtime_facade_contract(resolved_root),
        "rag_source_owner": {
            "consumer_exports": [
                {"bindings": sorted(bindings), "module": module}
                for module, bindings in exposed_bindings.items()
            ],
            "initializer_bindings": [
                {
                    "assignment_line": origin["assignment_line"],
                    "initializer": origin["initializer"],
                    "line": origin["line"],
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


def _validate_optional_sha256(value: object, context: str) -> None:
    if value is not None:
        _validate_sha256(value, context)


def _validate_signature(value: object, context: str) -> None:
    record = _require_dict(value, {
        "parameters", "return_annotation_sha256", "type_comment_sha256",
    }, context)
    parameters = _require_list(record["parameters"], f"{context}.parameters")
    names: set[str] = set()
    kinds: list[str] = []
    default_seen = False
    positional_kinds = {"positional_only", "positional_or_keyword"}
    rank = {
        "positional_only": 0,
        "positional_or_keyword": 1,
        "var_positional": 2,
        "keyword_only": 3,
        "var_keyword": 4,
    }
    for index, raw_parameter in enumerate(parameters):
        label = f"{context}.parameters[{index}]"
        parameter = _require_dict(raw_parameter, {
            "annotation_sha256", "default_sha256", "has_default", "kind",
            "name",
        }, label)
        name = _require_string(parameter["name"], f"{label}.name")
        if name in names:
            raise ArchitectureInventoryError(f"{label}.name is duplicated")
        names.add(name)
        kind = _require_string(parameter["kind"], f"{label}.kind")
        if kind not in rank:
            raise ArchitectureInventoryError(f"{label}.kind is invalid")
        kinds.append(kind)
        has_default = _require_bool(
            parameter["has_default"], f"{label}.has_default"
        )
        _validate_optional_sha256(
            parameter["annotation_sha256"], f"{label}.annotation_sha256"
        )
        _validate_optional_sha256(
            parameter["default_sha256"], f"{label}.default_sha256"
        )
        if has_default != (parameter["default_sha256"] is not None):
            raise ArchitectureInventoryError(f"{label} default fields differ")
        if kind in {"var_positional", "var_keyword"} and has_default:
            raise ArchitectureInventoryError(f"{label} variadic default is invalid")
        if kind in positional_kinds:
            if default_seen and not has_default:
                raise ArchitectureInventoryError(
                    f"{context} positional defaults are not trailing"
                )
            default_seen = default_seen or has_default
    if kinds != sorted(kinds, key=rank.__getitem__):
        raise ArchitectureInventoryError(f"{context} parameter kinds are unordered")
    if kinds.count("var_positional") > 1 or kinds.count("var_keyword") > 1:
        raise ArchitectureInventoryError(f"{context} repeats a variadic parameter")
    _validate_optional_sha256(
        record["return_annotation_sha256"],
        f"{context}.return_annotation_sha256",
    )
    _validate_optional_sha256(
        record["type_comment_sha256"], f"{context}.type_comment_sha256"
    )


def _validate_definition(
    value: object, context: str, *, function: bool,
) -> None:
    keys = {"name", "scope", "span"}
    if function:
        keys.update({"kind", "signature"})
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
        _validate_signature(record["signature"], f"{context}.signature")


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
        "occurrence_count", "paths", "source_counts",
    }, context)
    name = _require_string(record["name"], f"{context}.name")
    paths = _validate_sorted_strings(record["paths"], f"{context}.paths")
    if not paths or any(path.split(".", 1)[0] != name for path in paths):
        raise ArchitectureInventoryError(f"{context} paths do not match its name")
    contexts = _validate_sorted_strings(
        record["contexts"], f"{context}.contexts"
    )
    if contexts != ["load"]:
        raise ArchitectureInventoryError(f"{context}.contexts is invalid")
    if not _validate_sorted_strings(record["kinds"], f"{context}.kinds"):
        raise ArchitectureInventoryError(f"{context}.kinds cannot be empty")
    occurrence_count = _require_int(
        record["occurrence_count"], f"{context}.occurrence_count", 1
    )
    _validate_sha256(record["locations_sha256"], f"{context}.locations_sha256")
    source_counts = _require_list(
        record["source_counts"], f"{context}.source_counts"
    )
    observed_paths: list[str] = []
    observed_count = 0
    for index, raw_source in enumerate(source_counts):
        label = f"{context}.source_counts[{index}]"
        source_record = _require_dict(raw_source, {"count", "path"}, label)
        source = _validate_path(source_record["path"], f"{label}.path")
        if source not in tracked_paths:
            raise ArchitectureInventoryError(
                f"{label}.path is not tracked"
            )
        observed_paths.append(source)
        observed_count += _require_int(
            source_record["count"], f"{label}.count", 1
        )
    if observed_paths != sorted(set(observed_paths)) or observed_count != occurrence_count:
        raise ArchitectureInventoryError(
            f"{context}.source_counts is inconsistent"
        )


def _validate_rag_imports(
    value: object, context: str, *, allow_empty: bool = False,
) -> None:
    imports = _require_list(value, context)
    if not imports and not allow_empty:
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
    record = _require_dict(value, {
        "direct_imports", "facade_roots", "module", "path", "references",
    }, context)
    module = _require_string(record["module"], f"{context}.module")
    if known_modules is not None and module not in known_modules:
        raise ArchitectureInventoryError(f"{context}.module is not architectural")
    path = _validate_path(record["path"], f"{context}.path")
    if path not in tracked_paths:
        raise ArchitectureInventoryError(f"{context}.path is not tracked")
    _validate_rag_imports(
        record["direct_imports"], f"{context}.direct_imports", allow_empty=True
    )
    _validate_sorted_strings(record["facade_roots"], f"{context}.facade_roots")
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
    if not record["direct_imports"] and not references:
        raise ArchitectureInventoryError(f"{context} has no facade evidence")


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


def _validate_tracked_sources_v2(
    value: object,
) -> tuple[dict[str, list[str]], set[str]]:
    tracked = _require_dict(value, {"count", "groups"}, "tracked_sources")
    groups = _require_list(tracked["groups"], "tracked_sources.groups")
    if len(groups) != len(_GROUP_NAMES):
        raise ArchitectureInventoryError("tracked_sources.groups is incomplete")
    grouped: dict[str, list[str]] = {}
    all_paths: list[str] = []
    for index, expected_name in enumerate(_GROUP_NAMES):
        context = f"tracked_sources.groups[{index}]"
        group = _require_dict(groups[index], {"count", "name", "paths"}, context)
        if group["name"] != expected_name:
            raise ArchitectureInventoryError(f"{context}.name must be {expected_name}")
        paths = _validate_sorted_strings(group["paths"], f"{context}.paths")
        for path_index, path in enumerate(paths):
            _validate_path(path, f"{context}.paths[{path_index}]")
        if _require_int(group["count"], f"{context}.count") != len(paths):
            raise ArchitectureInventoryError(f"{context}.count is inconsistent")
        grouped[expected_name] = paths
        all_paths.extend(paths)
    if len(all_paths) != len(set(all_paths)):
        raise ArchitectureInventoryError("tracked source paths are duplicated")
    if _require_int(tracked["count"], "tracked_sources.count") != len(all_paths):
        raise ArchitectureInventoryError("tracked_sources.count is inconsistent")
    return grouped, set(all_paths)


def _validate_edge_records_v2(
    value: object, context: str, known_modules: set[str],
) -> list[dict[str, Any]]:
    raw_edges = _require_list(value, context)
    edges: list[dict[str, Any]] = []
    targets: list[str] = []
    allowed_kinds = {"conditional", "dynamic_constant", "runtime", "type_only"}
    for index, raw_edge in enumerate(raw_edges):
        label = f"{context}[{index}]"
        edge = _require_dict(raw_edge, {"kinds", "target"}, label)
        target = _require_string(edge["target"], f"{label}.target")
        if target not in known_modules:
            raise ArchitectureInventoryError(f"{label}.target is not architectural")
        kinds = _validate_sorted_strings(edge["kinds"], f"{label}.kinds")
        if not kinds or not set(kinds) <= allowed_kinds:
            raise ArchitectureInventoryError(f"{label}.kinds is invalid")
        edges.append(edge)
        targets.append(target)
    if targets != sorted(set(targets)):
        raise ArchitectureInventoryError(f"{context} must be target-sorted and unique")
    return edges


def _validate_source_architecture_v2(
    value: object, grouped_paths: Mapping[str, list[str]],
) -> set[str]:
    context = "source_architecture"
    architecture = _require_dict(value, {
        "class_count", "function_count", "import_graph", "module_count",
        "modules", "physical_line_count", "source_groups",
        "top_level_function_count",
    }, context)
    if architecture["source_groups"] != ["production", "tools", "other"]:
        raise ArchitectureInventoryError(
            "source_architecture.source_groups must name every non-test group"
        )
    raw_modules = _require_list(architecture["modules"], f"{context}.modules")
    preliminary: list[tuple[str, str, dict[str, Any]]] = []
    for index, raw_module in enumerate(raw_modules):
        label = f"{context}.modules[{index}]"
        module = _require_dict(raw_module, {
            "class_count", "classes", "function_count", "functions",
            "import_edges", "module", "non_blank_lines", "path",
            "physical_lines", "top_level_function_count",
        }, label)
        name = _require_string(module["module"], f"{label}.module")
        path = _validate_path(module["path"], f"{label}.path")
        preliminary.append((name, path, module))
    module_names = [item[0] for item in preliminary]
    module_paths = [item[1] for item in preliminary]
    if module_names != sorted(set(module_names)):
        raise ArchitectureInventoryError(
            "source_architecture.modules must be module-sorted and unique"
        )
    architecture_paths = {
        path
        for group in ("production", "tools", "other")
        for path in grouped_paths[group]
    }
    if set(module_paths) != architecture_paths or len(module_paths) != len(set(module_paths)):
        raise ArchitectureInventoryError(
            "source_architecture module/source paths differ"
        )
    known_modules = set(module_names)
    paths_by_module: dict[str, str] = {}
    edges_by_module: dict[str, list[dict[str, Any]]] = {}
    graph_sets: dict[str, set[str]] = {}
    function_total = 0
    top_level_total = 0
    class_total = 0
    line_total = 0
    for index, (name, path, module) in enumerate(preliminary):
        label = f"{context}.modules[{index}]"
        physical = _require_int(module["physical_lines"], f"{label}.physical_lines")
        non_blank = _require_int(module["non_blank_lines"], f"{label}.non_blank_lines")
        if non_blank > physical:
            raise ArchitectureInventoryError(f"{label} line counts are inconsistent")
        functions = _require_list(module["functions"], f"{label}.functions")
        function_keys: list[tuple[int, str, str]] = []
        for def_index, definition in enumerate(functions):
            def_label = f"{label}.functions[{def_index}]"
            _validate_definition(definition, def_label, function=True)
            function_keys.append((
                definition["span"][0], definition["name"], definition["kind"],
            ))
        if function_keys != sorted(function_keys):
            raise ArchitectureInventoryError(f"{label}.functions is not source-sorted")
        classes = _require_list(module["classes"], f"{label}.classes")
        class_keys: list[tuple[int, str]] = []
        for def_index, definition in enumerate(classes):
            def_label = f"{label}.classes[{def_index}]"
            _validate_definition(definition, def_label, function=False)
            class_keys.append((definition["span"][0], definition["name"]))
        if class_keys != sorted(class_keys):
            raise ArchitectureInventoryError(f"{label}.classes is not source-sorted")
        observed_top = sum(item["scope"] == "top_level" for item in functions)
        if _require_int(module["function_count"], f"{label}.function_count") != len(functions):
            raise ArchitectureInventoryError(f"{label}.function_count is inconsistent")
        if _require_int(module["class_count"], f"{label}.class_count") != len(classes):
            raise ArchitectureInventoryError(f"{label}.class_count is inconsistent")
        if _require_int(
            module["top_level_function_count"], f"{label}.top_level_function_count"
        ) != observed_top:
            raise ArchitectureInventoryError(
                f"{label}.top_level_function_count is inconsistent"
            )
        edges = _validate_edge_records_v2(
            module["import_edges"], f"{label}.import_edges", known_modules
        )
        paths_by_module[name] = path
        edges_by_module[name] = edges
        graph_sets[name] = {edge["target"] for edge in edges}
        function_total += len(functions)
        top_level_total += observed_top
        class_total += len(classes)
        line_total += physical
    expected_totals = {
        "module_count": len(preliminary),
        "function_count": function_total,
        "top_level_function_count": top_level_total,
        "class_count": class_total,
        "physical_line_count": line_total,
    }
    for field, expected in expected_totals.items():
        if _require_int(architecture[field], f"{context}.{field}") != expected:
            raise ArchitectureInventoryError(f"{context}.{field} is inconsistent")
    graph = _require_dict(architecture["import_graph"], {
        "cyclic_components", "edge_count", "edge_policy", "nodes",
        "strongly_connected_components",
    }, f"{context}.import_graph")
    if graph["edge_policy"] != "runtime+conditional+type-only+constant-dynamic":
        raise ArchitectureInventoryError(
            f"{context}.import_graph.edge_policy is invalid"
        )
    nodes = _require_list(graph["nodes"], f"{context}.import_graph.nodes")
    observed_node_names: list[str] = []
    for index, raw_node in enumerate(nodes):
        label = f"{context}.import_graph.nodes[{index}]"
        node = _require_dict(raw_node, {"edges", "module", "path"}, label)
        name = _require_string(node["module"], f"{label}.module")
        path = _validate_path(node["path"], f"{label}.path")
        edges = _validate_edge_records_v2(node["edges"], f"{label}.edges", known_modules)
        if name not in known_modules or path != paths_by_module[name]:
            raise ArchitectureInventoryError(f"{label} identity differs from module data")
        if edges != edges_by_module[name]:
            raise ArchitectureInventoryError(f"{label}.edges differs from module data")
        observed_node_names.append(name)
    if observed_node_names != sorted(known_modules):
        raise ArchitectureInventoryError(
            f"{context}.import_graph.nodes is incomplete or unsorted"
        )
    if _require_int(graph["edge_count"], f"{context}.import_graph.edge_count") != sum(
        len(edges) for edges in graph_sets.values()
    ):
        raise ArchitectureInventoryError(f"{context}.import_graph.edge_count is inconsistent")
    components = _strongly_connected_components(graph_sets)
    if graph["strongly_connected_components"] != components:
        raise ArchitectureInventoryError(
            f"{context}.import_graph strongly connected components differ"
        )
    cyclic = [
        component for component in components
        if len(component) > 1 or component[0] in graph_sets[component[0]]
    ]
    if graph["cyclic_components"] != cyclic:
        raise ArchitectureInventoryError(f"{context}.import_graph cyclic components differ")
    return known_modules


def _validate_rag_source_owner_v2(
    value: object, grouped_paths: Mapping[str, list[str]], known_modules: set[str],
) -> None:
    context = "rag_source_owner"
    owner = _require_dict(value, {
        "consumer_exports", "explicit_binding_count", "explicit_bindings",
        "import_star", "imported_bindings", "initializer_bindings",
        "local_definitions", "path",
    }, context)
    if owner["path"] != "rag.py" or "rag.py" not in grouped_paths["production"]:
        raise ArchitectureInventoryError(f"{context}.path must be tracked rag.py")
    bindings = _validate_sorted_strings(owner["explicit_bindings"], f"{context}.explicit_bindings")
    if _require_int(owner["explicit_binding_count"], f"{context}.explicit_binding_count") != len(bindings):
        raise ArchitectureInventoryError(f"{context}.explicit_binding_count is inconsistent")
    exports = _require_list(owner["consumer_exports"], f"{context}.consumer_exports")
    export_modules: list[str] = []
    for index, raw_export in enumerate(exports):
        label = f"{context}.consumer_exports[{index}]"
        export = _require_dict(raw_export, {"bindings", "module"}, label)
        module = _require_string(export["module"], f"{label}.module")
        if module not in known_modules or module == "rag":
            raise ArchitectureInventoryError(f"{label}.module is invalid")
        if not _validate_sorted_strings(export["bindings"], f"{label}.bindings"):
            raise ArchitectureInventoryError(f"{label}.bindings cannot be empty")
        export_modules.append(module)
    if export_modules != sorted(set(export_modules)):
        raise ArchitectureInventoryError(f"{context}.consumer_exports is not sorted/unique")
    initializers = _require_list(owner["initializer_bindings"], f"{context}.initializer_bindings")
    initializer_keys: list[tuple[str, int, str, int]] = []
    for index, raw_initializer in enumerate(initializers):
        label = f"{context}.initializer_bindings[{index}]"
        initializer = _require_dict(raw_initializer, {
            "assignment_line", "initializer", "line", "name",
        }, label)
        name = _require_string(initializer["name"], f"{label}.name")
        if name not in bindings:
            raise ArchitectureInventoryError(f"{label}.name is not a source binding")
        called = _require_string(initializer["initializer"], f"{label}.initializer")
        line = _require_int(initializer["line"], f"{label}.line", 1)
        assignment_line = _require_int(
            initializer["assignment_line"], f"{label}.assignment_line", 1
        )
        initializer_keys.append((name, line, called, assignment_line))
    if initializer_keys != sorted(initializer_keys):
        raise ArchitectureInventoryError(f"{context}.initializer_bindings is not sorted")
    imported = _require_list(owner["imported_bindings"], f"{context}.imported_bindings")
    import_keys: list[tuple[int, str, str, str, str]] = []
    for index, raw_import in enumerate(imported):
        label = f"{context}.imported_bindings[{index}]"
        _validate_import_record(raw_import, label)
        if raw_import["binding"] not in bindings:
            raise ArchitectureInventoryError(f"{label}.binding is not a source binding")
        import_keys.append((
            raw_import["line"], raw_import["binding"], raw_import["kind"],
            raw_import["source"], str(raw_import.get("imported", "")),
        ))
    if import_keys != sorted(import_keys):
        raise ArchitectureInventoryError(f"{context}.imported_bindings is not source-sorted")
    local = _require_list(owner["local_definitions"], f"{context}.local_definitions")
    local_keys: list[tuple[int, str]] = []
    for index, raw_definition in enumerate(local):
        label = f"{context}.local_definitions[{index}]"
        definition = _require_dict(raw_definition, {"kind", "name", "span"}, label)
        if definition["kind"] not in {"async_function", "class", "function"}:
            raise ArchitectureInventoryError(f"{label}.kind is invalid")
        name = _require_string(definition["name"], f"{label}.name")
        if name not in bindings:
            raise ArchitectureInventoryError(f"{label}.name is not a source binding")
        span = _require_list(definition["span"], f"{label}.span")
        if len(span) != 2:
            raise ArchitectureInventoryError(f"{label}.span must have two lines")
        first = _require_int(span[0], f"{label}.span[0]", 1)
        _require_int(span[1], f"{label}.span[1]", first)
        local_keys.append((first, name))
    if local_keys != sorted(local_keys):
        raise ArchitectureInventoryError(f"{context}.local_definitions is not source-sorted")
    import_star = _require_dict(owner["import_star"], {"names", "uses_explicit_all"}, f"{context}.import_star")
    names = _validate_sorted_strings(import_star["names"], f"{context}.import_star.names")
    uses_all = _require_bool(import_star["uses_explicit_all"], f"{context}.import_star.uses_explicit_all")
    if not uses_all and names != [name for name in bindings if not name.startswith("_")]:
        raise ArchitectureInventoryError(f"{context}.import_star is inconsistent")


def _validate_runtime_contract_v2(value: object) -> None:
    context = "rag_runtime_contract"
    contract = _require_dict(value, {
        "behavior", "callables", "dir_names", "import_star_names",
        "interpreter_variant_exclusions", "logger_name", "module_type",
        "vars_names",
    }, context)
    expected_behaviors = {
        "dynamic_add_read", "dynamic_delete", "existing_assignment_restored",
        "existing_assignment_visible", "existing_deletion_restored",
        "existing_deletion_visible", "nested_assignment_restored",
        "nested_assignment_visible", "reload_preserves_dynamic",
        "reload_reexecutes_existing_binding", "reload_same_module",
    }
    behavior = _require_dict(contract["behavior"], expected_behaviors, f"{context}.behavior")
    for name in sorted(expected_behaviors):
        if not _require_bool(behavior[name], f"{context}.behavior.{name}"):
            raise ArchitectureInventoryError(f"{context}.behavior.{name} must hold")
    for field in ("dir_names", "import_star_names", "vars_names"):
        _validate_sorted_strings(contract[field], f"{context}.{field}")
    variants = _validate_sorted_strings(
        contract["interpreter_variant_exclusions"],
        f"{context}.interpreter_variant_exclusions",
    )
    if variants != sorted(_INTERPRETER_VARIANT_NAMES):
        raise ArchitectureInventoryError(f"{context}.interpreter_variant_exclusions is invalid")
    _require_string(contract["logger_name"], f"{context}.logger_name")
    _require_string(contract["module_type"], f"{context}.module_type")
    callables = _require_list(contract["callables"], f"{context}.callables")
    callable_names: list[str] = []
    for index, raw_callable in enumerate(callables):
        label = f"{context}.callables[{index}]"
        item = _require_dict(raw_callable, {
            "kind", "module", "name", "pickle_identity", "qualname",
            "runtime_signature_sha256", "runtime_signature_status",
            "type_hints_sha256", "type_hints_status",
        }, label)
        if item["kind"] not in {"class", "function"}:
            raise ArchitectureInventoryError(f"{label}.kind is invalid")
        name = _require_string(item["name"], f"{label}.name")
        _require_string(item["module"], f"{label}.module")
        _require_string(item["qualname"], f"{label}.qualname")
        if item["pickle_identity"] not in {"different", "same", "unsupported"}:
            raise ArchitectureInventoryError(f"{label}.pickle_identity is invalid")
        if item["runtime_signature_status"] not in {
            "interpreter_owned", "ok", "unsupported",
        }:
            raise ArchitectureInventoryError(f"{label}.runtime_signature_status is invalid")
        if item["type_hints_status"] not in {
            "interpreter_owned", "ok", "unresolved",
        }:
            raise ArchitectureInventoryError(f"{label}.type_hints_status is invalid")
        _validate_sha256(item["runtime_signature_sha256"], f"{label}.runtime_signature_sha256")
        _validate_sha256(item["type_hints_sha256"], f"{label}.type_hints_sha256")
        callable_names.append(name)
    if callable_names != sorted(set(callable_names)):
        raise ArchitectureInventoryError(f"{context}.callables is not name-sorted/unique")


def _validate_operation_site_v2(
    value: object, context: str, tracked_test_paths: set[str],
    allowed_operations: set[str],
) -> tuple[str, int, str]:
    site = _require_dict(value, {"line", "operation", "source"}, context)
    line = _require_int(site["line"], f"{context}.line", 1)
    operation = _require_string(site["operation"], f"{context}.operation")
    if operation not in allowed_operations:
        raise ArchitectureInventoryError(f"{context}.operation is invalid")
    source = _validate_path(site["source"], f"{context}.source")
    if source not in tracked_test_paths:
        raise ArchitectureInventoryError(f"{context}.source is not a tracked test")
    return source, line, operation


def _validate_target_inventory_v2(
    value: object, context: str, tracked_test_paths: set[str],
    allowed_operations: set[str],
) -> None:
    inventory = _require_dict(value, {"dynamic_sites", "nested", "top_level"}, context)
    dynamic = _require_list(inventory["dynamic_sites"], f"{context}.dynamic_sites")
    dynamic_keys = [
        _validate_operation_site_v2(
            site, f"{context}.dynamic_sites[{index}]", tracked_test_paths,
            allowed_operations,
        )
        for index, site in enumerate(dynamic)
    ]
    if dynamic_keys != sorted(dynamic_keys):
        raise ArchitectureInventoryError(f"{context}.dynamic_sites is not sorted")
    all_targets: set[str] = set()
    for category in ("top_level", "nested"):
        entries = _require_list(inventory[category], f"{context}.{category}")
        targets: list[str] = []
        for index, raw_entry in enumerate(entries):
            label = f"{context}.{category}[{index}]"
            entry = _require_dict(raw_entry, {
                "locations_sha256", "occurrence_count", "operations",
                "source_counts", "target",
            }, label)
            target = _require_string(entry["target"], f"{label}.target")
            parts = target.split(".")
            if any(not part for part in parts) or ((len(parts) > 1) != (category == "nested")):
                raise ArchitectureInventoryError(f"{label}.target depth is invalid")
            operations = _validate_sorted_strings(entry["operations"], f"{label}.operations")
            if not operations or not set(operations) <= allowed_operations:
                raise ArchitectureInventoryError(f"{label}.operations is invalid")
            occurrence_count = _require_int(entry["occurrence_count"], f"{label}.occurrence_count", 1)
            _validate_sha256(entry["locations_sha256"], f"{label}.locations_sha256")
            sources = _require_list(entry["source_counts"], f"{label}.source_counts")
            source_keys: list[str] = []
            observed_count = 0
            for source_index, raw_source in enumerate(sources):
                source_label = f"{label}.source_counts[{source_index}]"
                source_record = _require_dict(raw_source, {"count", "path"}, source_label)
                path = _validate_path(source_record["path"], f"{source_label}.path")
                if path not in tracked_test_paths:
                    raise ArchitectureInventoryError(f"{source_label}.path is not a tracked test")
                source_keys.append(path)
                observed_count += _require_int(source_record["count"], f"{source_label}.count", 1)
            if source_keys != sorted(set(source_keys)) or observed_count != occurrence_count:
                raise ArchitectureInventoryError(f"{label}.source_counts is inconsistent")
            if target in all_targets:
                raise ArchitectureInventoryError(f"{context} target is duplicated")
            all_targets.add(target)
            targets.append(target)
        if targets != sorted(targets):
            raise ArchitectureInventoryError(f"{context}.{category} is not sorted")


def _validate_consumers_v2(
    value: object, tracked_paths: set[str], grouped_paths: Mapping[str, list[str]],
    known_modules: set[str],
) -> None:
    consumers = _require_dict(value, {"production", "tests"}, "consumers")
    production = _require_list(consumers["production"], "consumers.production")
    production_modules: list[str] = []
    for index, consumer in enumerate(production):
        _validate_consumer(
            consumer, f"consumers.production[{index}]", tracked_paths,
            known_modules,
        )
        production_modules.append(consumer["module"])
    if production_modules != sorted(set(production_modules)) or "rag" in production_modules:
        raise ArchitectureInventoryError("consumers.production is not sorted/unique")
    tests = _require_dict(consumers["tests"], {
        "direct_mutations", "modules", "monkeypatch_seams",
        "private_read_references", "using_module_count",
    }, "consumers.tests")
    tracked_test_paths = set(grouped_paths["tests"])
    modules = _require_list(tests["modules"], "consumers.tests.modules")
    module_keys: list[tuple[str, str]] = []
    for index, raw_module in enumerate(modules):
        label = f"consumers.tests.modules[{index}]"
        module = _require_dict(raw_module, {"facade_roots", "module", "path"}, label)
        name = _require_string(module["module"], f"{label}.module")
        path = _validate_path(module["path"], f"{label}.path")
        if path not in tracked_test_paths:
            raise ArchitectureInventoryError(f"{label}.path is not a tracked test")
        _validate_sorted_strings(module["facade_roots"], f"{label}.facade_roots")
        module_keys.append((name, path))
    if module_keys != sorted(set(module_keys)):
        raise ArchitectureInventoryError("consumers.tests.modules is not sorted/unique")
    if _require_int(tests["using_module_count"], "consumers.tests.using_module_count") != len(modules):
        raise ArchitectureInventoryError("consumers.tests.using_module_count is inconsistent")
    private = _require_list(tests["private_read_references"], "consumers.tests.private_read_references")
    private_names: list[str] = []
    for index, reference in enumerate(private):
        _validate_reference_group(
            reference, f"consumers.tests.private_read_references[{index}]",
            tracked_test_paths,
        )
        name = reference["name"]
        if not name.startswith("_"):
            raise ArchitectureInventoryError(
                f"consumers.tests.private_read_references[{index}] is not private"
            )
        private_names.append(name)
    if private_names != sorted(set(private_names)):
        raise ArchitectureInventoryError("private read references are not sorted/unique")
    _validate_target_inventory_v2(
        tests["monkeypatch_seams"], "consumers.tests.monkeypatch_seams",
        tracked_test_paths, {"delattr", "patch", "patch.object", "setattr"},
    )
    _validate_target_inventory_v2(
        tests["direct_mutations"], "consumers.tests.direct_mutations",
        tracked_test_paths, {"assign", "delattr", "delete", "setattr"},
    )


def validate_inventory(value: object) -> None:
    """Validate schema v2 and all content-free cross-field invariants."""
    document = _require_dict(value, {
        "analysis_model", "consumers", "interpreter_normalization",
        "rag_runtime_contract", "rag_source_owner", "schema_version",
        "source_architecture", "tracked_sources",
    }, "inventory")
    if _require_int(document["schema_version"], "inventory.schema_version", 1) != SCHEMA_VERSION:
        raise ArchitectureInventoryError(
            f"inventory.schema_version must be {SCHEMA_VERSION}"
        )
    if document["analysis_model"] != ANALYSIS_MODEL:
        raise ArchitectureInventoryError(
            f"inventory.analysis_model must be {ANALYSIS_MODEL}"
        )
    if document["interpreter_normalization"] != INTERPRETER_NORMALIZATION:
        raise ArchitectureInventoryError(
            "inventory.interpreter_normalization is unsupported"
        )
    grouped_paths, tracked_paths = _validate_tracked_sources_v2(
        document["tracked_sources"]
    )
    known_modules = _validate_source_architecture_v2(
        document["source_architecture"], grouped_paths
    )
    _validate_rag_source_owner_v2(
        document["rag_source_owner"], grouped_paths, known_modules
    )
    _validate_runtime_contract_v2(document["rag_runtime_contract"])
    _validate_consumers_v2(
        document["consumers"], tracked_paths, grouped_paths, known_modules
    )


def inventory_bytes(value: object) -> bytes:
    """Return reviewable canonical, newline-terminated UTF-8 inventory bytes."""
    validate_inventory(value)
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


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
    baseline_value = load_inventory(path)
    expected = inventory_bytes(baseline_value)
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
        changed_sections = [
            section
            for section in sorted(current)
            if _canonical_sha256(current[section])
            != _canonical_sha256(baseline_value[section])
        ]
        baseline_counts = _inventory_counts(baseline_value)
        current_counts = _inventory_counts(current)
        changed_counts = [
            f"{name} {baseline_counts[name]}->{current_counts[name]}"
            for name in baseline_counts
            if baseline_counts[name] != current_counts[name]
        ]
        count_detail = "; ".join(changed_counts) or "aggregate counts unchanged"
        raise ArchitectureInventoryError(
            "Architecture inventory differs from the committed baseline; "
            f"changed sections: {', '.join(changed_sections)}; {count_detail}; "
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


def _inventory_counts(value: Mapping[str, Any]) -> dict[str, int]:
    architecture = value["source_architecture"]
    owner = value["rag_source_owner"]
    runtime = value["rag_runtime_contract"]
    tests = value["consumers"]["tests"]
    patches = tests["monkeypatch_seams"]
    mutations = tests["direct_mutations"]
    return {
        "tracked_sources": value["tracked_sources"]["count"],
        "architecture_modules": architecture["module_count"],
        "architecture_functions": architecture["function_count"],
        "architecture_classes": architecture["class_count"],
        "rag_source_bindings": owner["explicit_binding_count"],
        "rag_runtime_bindings": len(runtime["vars_names"]),
        "rag_runtime_callables": len(runtime["callables"]),
        "production_consumers": len(value["consumers"]["production"]),
        "test_consumers": tests["using_module_count"],
        "patch_targets": len(patches["top_level"]) + len(patches["nested"]),
        "direct_mutation_targets": (
            len(mutations["top_level"]) + len(mutations["nested"])
        ),
    }


def _summary(value: Mapping[str, Any]) -> str:
    counts = _inventory_counts(value)
    tests = value["consumers"]["tests"]
    patches = tests["monkeypatch_seams"]
    return (
        f"{counts['tracked_sources']} tracked Python sources, "
        f"{counts['architecture_modules']} non-test modules, "
        f"{counts['architecture_functions']} functions, "
        f"{counts['rag_source_bindings']} static and "
        f"{counts['rag_runtime_bindings']} runtime rag bindings, "
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
