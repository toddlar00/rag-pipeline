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
import sys
import tempfile
import textwrap
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = Path("architecture-inventory.json")
SCHEMA_VERSION = 3
ANALYSIS_MODEL = "tracked-python-ast-v3"
INTERPRETER_NORMALIZATION = "cpython-3.12-3.14-platform-neutral-v2"
HASH_DOMAINS = {
    "definition_locations": "rag-pipeline:architecture:definition-locations:v1",
    "runtime_symbol_contract": "rag-pipeline:architecture:runtime-symbol-contract:v1",
    "static_signature": "rag-pipeline:architecture:static-signature:v1",
}
_EDGE_CONTEXTS = ("conditional", "runtime", "type_only")
_EDGE_ORIGINS = ("dynamic", "static")
_PLATFORM_VARIANT_BINDINGS = {
    "getpass": "python-stdlib:getpass:platform-dispatch:v1",
}
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
socket.getfqdn = denied_network
socket.gethostbyaddr = denied_network
socket.gethostbyname = denied_network
socket.gethostbyname_ex = denied_network
socket.getnameinfo = denied_network
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
        rendered = os.fspath(value).replace("\\", "/")
        parts = [part for part in rendered.split("/") if part not in {"", "."}]
        return {
            "absolute": rendered.startswith("/") or (
                len(rendered) > 2 and rendered[1:3] == ":/"
            ),
            "kind": "path",
            "value_sha256": digest(parts),
        }
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
platform_variant_tokens = {
    "getpass": "python-stdlib:getpass:platform-dispatch:v1",
}
platform_variant_bindings = []
for name, token in sorted(platform_variant_tokens.items()):
    value = vars(rag).get(name)
    if value is None:
        continue
    if (
        getattr(value, "__module__", None) != "getpass"
        or getattr(value, "__qualname__", None)
        not in {"unix_getpass", "win_getpass"}
    ):
        raise RuntimeError("unexpected platform-variant facade binding")
    platform_variant_bindings.append({"name": name, "token": token})
vars_names = sorted(name for name in vars(rag) if name not in variant_names)
dir_names = sorted(name for name in dir(rag) if name not in variant_names)
callables = [
    callable_record(name, value)
    for name, value in sorted(vars(rag).items())
    if name not in variant_names
    and name not in platform_variant_tokens
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
    "platform_variant_bindings": platform_variant_bindings,
    "vars_names": vars_names,
}
sys.stdout.write(json.dumps(
    result, allow_nan=False, ensure_ascii=False,
    separators=(",", ":"), sort_keys=True,
))
'''

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import process_supervision  # noqa: E402
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


def _domain_sha256(domain: str, value: object) -> str:
    return hashlib.sha256(
        domain.encode("utf-8") + b"\0" + _canonical_json_bytes(value)
    ).hexdigest()


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
    """Collect first-party imports with execution context and edge provenance.

    The small alias environment is deliberately flow-sensitive.  It recognizes
    only importlib/typing objects whose provenance is known, and drops that
    provenance as soon as a name is rebound.  This avoids treating an arbitrary
    ``object.import_module(...)`` call as an import edge.
    """

    _IMPORTLIB = "importlib_module"
    _IMPORT_MODULE = "import_module_function"
    _TYPING = "typing_module"
    _TYPE_CHECKING = "type_checking_constant"
    _BUILTIN_IMPORT = "builtin_import"

    def __init__(
        self, current: str, path: Path, known: set[str],
    ) -> None:
        self.current = current
        self.path = path
        self.known = known
        self.context = "runtime"
        self.edges: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.aliases: dict[str, str] = {"__import__": self._BUILTIN_IMPORT}
        self.scope_map: _ScopeMap | None = None

    def visit_Module(self, node: ast.Module) -> None:
        self.scope_map = _ScopeMap(node)
        for statement in node.body:
            self.visit(statement)

    def _with_context(self, context: str, nodes: Iterable[ast.AST]) -> None:
        previous = self.context
        self.context = context
        for node in nodes:
            self.visit(node)
        self.context = previous

    def _conditional_context(self) -> str:
        return "type_only" if self.context == "type_only" else "conditional"

    def _add(self, targets: Iterable[str], origin: str) -> None:
        for target in targets:
            self.edges[target].add((self.context, origin))

    def _role(self, value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            return self.aliases.get(value.id)
        if isinstance(value, ast.Attribute) and value.attr == "import_module":
            if self._role(value.value) == self._IMPORTLIB:
                return self._IMPORT_MODULE
        if isinstance(value, ast.Attribute) and value.attr == "TYPE_CHECKING":
            if self._role(value.value) == self._TYPING:
                return self._TYPE_CHECKING
        return None

    def _bind(self, target: ast.AST, role: str | None) -> None:
        for name in _target_names(target):
            if role is None:
                self.aliases.pop(name, None)
            else:
                self.aliases[name] = role

    def _visit_branch(
        self, nodes: Iterable[ast.AST], context: str,
        aliases: Mapping[str, str],
    ) -> dict[str, str]:
        previous_aliases = self.aliases
        self.aliases = dict(aliases)
        self._with_context(context, nodes)
        result = self.aliases
        self.aliases = previous_aliases
        return result

    @staticmethod
    def _merge_aliases(*branches: Mapping[str, str]) -> dict[str, str]:
        if not branches:
            return {}
        names = set.intersection(*(set(branch) for branch in branches))
        return {
            name: branches[0][name]
            for name in names
            if all(branch[name] == branches[0][name] for branch in branches[1:])
        }

    def _type_checking_guard(self, node: ast.AST) -> bool:
        return self._role(node) == self._TYPE_CHECKING

    def _package_name(self, expression: ast.AST | None) -> str | None:
        if expression is None:
            return None
        if isinstance(expression, ast.Name) and expression.id == "__package__":
            return (
                self.current
                if self.path.name == "__init__.py"
                else self.current.rpartition(".")[0]
            )
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return expression.value
        return None

    def _dynamic_name(self, node: ast.Call) -> str | None:
        role = self._role(node.func)
        if role not in {self._IMPORT_MODULE, self._BUILTIN_IMPORT} or not node.args:
            return None
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            return None
        name = first.value
        if role == self._BUILTIN_IMPORT or not name.startswith("."):
            return name if not name.startswith(".") else None
        package_expression = node.args[1] if len(node.args) > 1 else None
        if package_expression is None:
            package_expression = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "package"),
                None,
            )
        package = self._package_name(package_expression)
        if not package:
            return None
        level = len(name) - len(name.lstrip("."))
        package_parts = package.split(".")
        if level > len(package_parts):
            return None
        prefix = package_parts[:len(package_parts) - level + 1]
        suffix = name[level:]
        if suffix:
            prefix.extend(suffix.split("."))
        return ".".join(prefix)

    def visit_Import(self, node: ast.Import) -> None:
        self._add(
            _import_targets(self.current, self.path, node, self.known), "static"
        )
        for alias in node.names:
            binding = alias.asname or alias.name.split(".")[0]
            if alias.name == "importlib":
                self.aliases[binding] = self._IMPORTLIB
            elif alias.name == "typing":
                self.aliases[binding] = self._TYPING
            else:
                self.aliases.pop(binding, None)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._add(
            _import_targets(self.current, self.path, node, self.known), "static"
        )
        base = _absolute_from_import(self.current, self.path, node)
        for alias in node.names:
            if alias.name == "*":
                continue
            binding = alias.asname or alias.name
            role = None
            if base == "importlib" and alias.name == "import_module":
                role = self._IMPORT_MODULE
            elif base == "typing" and alias.name == "TYPE_CHECKING":
                role = self._TYPE_CHECKING
            if role is None:
                self.aliases.pop(binding, None)
            else:
                self.aliases[binding] = role

    def visit_Call(self, node: ast.Call) -> None:
        dynamic_name = self._dynamic_name(node)
        if dynamic_name:
            self._add(_known_prefixes(dynamic_name, self.known), "dynamic")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        role = self._role(node.value)
        for target in node.targets:
            self._bind(target, role)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._bind(node.target, self._role(node.value))
        else:
            self._bind(node.target, None)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind(node.target, self._role(node.value))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        self._bind(node.target, None)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._bind(target, None)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        starting = dict(self.aliases)
        if self._type_checking_guard(node.test):
            body = self._visit_branch(node.body, "type_only", starting)
            otherwise = self._visit_branch(node.orelse, self.context, starting)
        else:
            context = self._conditional_context()
            body = self._visit_branch(node.body, context, starting)
            otherwise = self._visit_branch(node.orelse, context, starting)
        self.aliases = self._merge_aliases(body, otherwise)

    def _visit_function_header(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        arguments = node.args
        for argument in [
            *arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
        ]:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if arguments.vararg is not None and arguments.vararg.annotation is not None:
            self.visit(arguments.vararg.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
            self.visit(arguments.kwarg.annotation)
        for default in [
            *arguments.defaults,
            *(item for item in arguments.kw_defaults if item is not None),
        ]:
            self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

    def _function_locals(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    ) -> set[str]:
        names = {
            argument.arg
            for argument in [
                *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
            ]
        }
        if node.args.vararg is not None:
            names.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            names.add(node.args.kwarg.arg)

        globals_: set[str] = set()
        nonlocals: set[str] = set()
        if self.scope_map is None:
            raise ArchitectureInventoryError("import scope analysis is unavailable")
        for item, owner in self.scope_map.node_scope.items():
            if owner is not node:
                continue
            if isinstance(item, ast.Name) and isinstance(
                item.ctx, (ast.Store, ast.Del)
            ):
                names.add(item.id)
            elif isinstance(item, ast.Import):
                names.update(
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in item.names
                )
            elif isinstance(item, ast.ImportFrom):
                names.update(
                    alias.asname or alias.name
                    for alias in item.names if alias.name != "*"
                )
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(item.name)
            elif isinstance(item, ast.ExceptHandler) and item.name:
                names.add(item.name)
            elif isinstance(item, ast.Global):
                globals_.update(item.names)
            elif isinstance(item, ast.Nonlocal):
                nonlocals.update(item.names)
        return names - globals_ - nonlocals

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self._visit_function_header(node)
        outer = self.aliases
        local = dict(outer)
        for name in self._function_locals(node):
            local.pop(name, None)
        self.aliases = local
        self._with_context(self._conditional_context(), node.body)
        self.aliases = outer
        self.aliases.pop(node.name, None)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [
            *node.args.defaults,
            *(item for item in node.args.kw_defaults if item is not None),
        ]:
            self.visit(default)
        outer = self.aliases
        local = dict(outer)
        for name in self._function_locals(node):
            local.pop(name, None)
        self.aliases = local
        self._with_context(self._conditional_context(), (node.body,))
        self.aliases = outer

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        for expression in [*node.bases, *(item.value for item in node.keywords)]:
            self.visit(expression)
        outer = self.aliases
        self.aliases = dict(outer)
        self._with_context(self.context, node.body)
        self.aliases = outer
        self.aliases.pop(node.name, None)

    def _visit_loop(
        self, eager: Iterable[ast.AST], target: ast.AST | None,
        conditional: Iterable[ast.AST],
    ) -> None:
        for node in eager:
            self.visit(node)
        starting = dict(self.aliases)
        previous_aliases = self.aliases
        self.aliases = dict(starting)
        if target is not None:
            self.visit(target)
            self._bind(target, None)
        self._with_context(self._conditional_context(), conditional)
        executed = self.aliases
        self.aliases = previous_aliases
        self.aliases = self._merge_aliases(starting, executed)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop((node.iter,), node.target, (*node.body, *node.orelse))

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop((node.iter,), node.target, (*node.body, *node.orelse))

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop((node.test,), None, (*node.body, *node.orelse))

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        results: Iterable[ast.AST],
    ) -> None:
        first, *remaining = node.generators
        self.visit(first.iter)
        outer = self.aliases
        self.aliases = dict(outer)
        self.visit(first.target)
        self._bind(first.target, None)
        for condition in first.ifs:
            self.visit(condition)
        for generator in remaining:
            self.visit(generator.iter)
            self.visit(generator.target)
            self._bind(generator.target, None)
            for condition in generator.ifs:
                self.visit(condition)
        for result in results:
            self.visit(result)
        self.aliases = outer

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_uncertain_block(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_uncertain_block(node)

    def _visit_uncertain_block(self, node: ast.Try | ast.TryStar) -> None:
        starting = dict(self.aliases)
        branches = [
            self._visit_branch(node.body, self._conditional_context(), starting),
            *(self._visit_branch(handler.body, self._conditional_context(), starting)
              for handler in node.handlers),
        ]
        merged = self._merge_aliases(*branches)
        after_else = self._visit_branch(node.orelse, self._conditional_context(), merged)
        self.aliases = self._visit_branch(
            node.finalbody, self._conditional_context(), after_else
        )

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        starting = dict(self.aliases)
        branches = [starting]
        for case in node.cases:
            branch_nodes: list[ast.AST] = []
            if case.guard is not None:
                branch_nodes.append(case.guard)
            branch_nodes.extend(case.body)
            branches.append(self._visit_branch(
                branch_nodes, self._conditional_context(), starting
            ))
        self.aliases = self._merge_aliases(*branches)


def _module_import_edges(
    module: str, path: Path, tree: ast.Module, known: set[str],
) -> dict[str, set[tuple[str, str]]]:
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
        "type_parameters_sha256": [
            _ast_identity(parameter)
            for parameter in getattr(node, "type_params", ())
        ],
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


def _compact_definitions(
    module: str,
    functions: Sequence[Mapping[str, object]],
    classes: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], str]:
    compact_functions = sorted((
        {
            "kind": item["kind"],
            "name": item["name"],
            "scope": item["scope"],
            "signature_sha256": _domain_sha256(
                HASH_DOMAINS["static_signature"],
                {
                    "kind": item["kind"],
                    "module": module,
                    "name": item["name"],
                    "signature": item["signature"],
                },
            ),
        }
        for item in functions
    ), key=lambda item: (item["name"], item["kind"]))
    compact_classes = sorted((
        {"name": item["name"], "scope": item["scope"]}
        for item in classes
    ), key=lambda item: item["name"])
    locations = [
        {
            "kind": item["kind"],
            "name": item["name"],
            "scope": item["scope"],
            "span": item["span"],
        }
        for item in functions
    ]
    locations.extend(
        {
            "kind": "class",
            "name": item["name"],
            "scope": item["scope"],
            "span": item["span"],
        }
        for item in classes
    )
    locations.sort(key=lambda item: (item["span"][0], item["name"], item["kind"]))
    return (
        compact_functions,
        compact_classes,
        _domain_sha256(
            HASH_DOMAINS["definition_locations"],
            {"definitions": locations, "module": module},
        ),
    )


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
        type_alias_type = getattr(ast, "TypeAlias", ())
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
            elif type_alias_type and isinstance(statement, type_alias_type):
                for name in _target_names(statement.name):
                    add(name, "type_alias", statement.lineno)
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
        elif isinstance(node, ast.Delete) and any(
            _target_names(target) for target in node.targets
        ):
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
    def executed_at_module(scope: ast.AST) -> bool:
        current: ast.AST | None = scope
        while isinstance(current, _ScopeMap._COMPREHENSIONS):
            current = scope_map.parent_scope[current]
        return current is tree

    nodes = [
        node for node, scope in scope_map.node_scope.items()
        if executed_at_module(scope)
    ]

    def root_name(node: ast.AST) -> str | None:
        current = node
        while isinstance(current, (ast.Attribute, ast.Subscript, ast.Starred)):
            current = current.value
        return current.id if isinstance(current, ast.Name) else None

    aliases = {"__all__"}
    mutator_aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in nodes:
            pairs: list[tuple[ast.AST, ast.AST]] = []
            if isinstance(node, ast.Assign):
                pairs.extend((target, node.value) for target in node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                pairs.append((node.target, node.value))
            elif isinstance(node, ast.NamedExpr):
                pairs.append((node.target, node.value))
            for target, value in pairs:
                if not isinstance(target, ast.Name):
                    continue
                source_root = root_name(value)
                if source_root in aliases and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
                if (
                    isinstance(value, ast.Attribute)
                    and root_name(value) in aliases
                    and target.id not in mutator_aliases
                ):
                    mutator_aliases.add(target.id)
                    changed = True
                if (
                    isinstance(value, ast.Name)
                    and value.id in mutator_aliases
                    and target.id not in mutator_aliases
                ):
                    mutator_aliases.add(target.id)
                    changed = True

    candidates: list[tuple[ast.AST, ast.AST | None]] = []
    ambiguous = False
    for node in nodes:
        if isinstance(node, ast.Assign):
            direct = [
                target for target in node.targets
                if isinstance(target, ast.Name) and target.id == "__all__"
            ]
            if direct:
                candidates.append((node, node.value))
                if len(node.targets) != 1:
                    ambiguous = True
            if any(
                not isinstance(target, ast.Name) and root_name(target) in aliases
                for target in node.targets
            ):
                ambiguous = True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                candidates.append((node, node.value))
            elif root_name(node.target) in aliases:
                ambiguous = True
        elif isinstance(node, ast.AugAssign) and root_name(node.target) in aliases:
            ambiguous = True
        elif isinstance(node, ast.NamedExpr) and root_name(node.target) in aliases:
            ambiguous = True
        elif isinstance(node, ast.Delete) and any(
            root_name(target) in aliases for target in node.targets
        ):
            ambiguous = True
        elif isinstance(node, ast.Call):
            function_root = root_name(node.func)
            if (
                function_root in aliases
                or (isinstance(node.func, ast.Name)
                    and node.func.id in mutator_aliases)
                or any(root_name(argument) in aliases for argument in node.args)
                or any(root_name(keyword.value) in aliases for keyword in node.keywords)
            ):
                ambiguous = True
    if not candidates and not ambiguous:
        return False, []
    target, value = candidates[0] if candidates else (tree, None)
    if (
        ambiguous
        or
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
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
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
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
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

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        result_nodes: Iterable[ast.AST],
    ) -> None:
        # Python evaluates only the leftmost iterable in the enclosing scope;
        # iteration targets, filters, later iterables, and result expressions
        # execute in the implicit comprehension scope.
        first, *remaining = node.generators
        self.visit(first.iter)
        outer = self.current
        self.parent_scope[node] = outer
        self.scopes.append(node)
        self.current = node
        self.visit(first.target)
        for condition in first.ifs:
            self.visit(condition)
        for generator in remaining:
            self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for result in result_nodes:
            self.visit(result)
        self.current = outer

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, [node.key, node.value])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, [node.elt])

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        evaluation_scope = self.current
        self.visit(node.value)
        binding_scope = evaluation_scope
        while isinstance(binding_scope, self._COMPREHENSIONS):
            parent = self.parent_scope[binding_scope]
            if parent is None:
                break
            binding_scope = parent
        self.node_scope[node] = binding_scope
        self.node_scope[node.target] = binding_scope


class _FacadeAnalysis(ast.NodeVisitor):
    """Flow-sensitive resolution of direct and re-exported ``rag`` values."""

    # A rule maps an attribute prefix on a local root to a rag capability.
    # ``import consumer as layer`` can therefore map ``layer.facade`` to the
    # facade root while ``from rag import operating as op`` maps ``op`` to the
    # ``operating`` capability directly.
    _Rule = tuple[tuple[str, ...], tuple[str, ...]]

    def __init__(
        self,
        tree: ast.Module,
        exposed_bindings: Mapping[str, Mapping[str, tuple[str, ...]]],
    ) -> None:
        self.tree = tree
        self.exposed_bindings = exposed_bindings
        self.scope_map = _ScopeMap(tree)
        self.environment: dict[str, set[_FacadeAnalysis._Rule]] = {}
        self.resolved: dict[ast.AST, tuple[str, ...]] = {}
        self._roots: set[str] = set()
        self._module_environment: dict[str, set[_FacadeAnalysis._Rule]] = {}
        self._class_lexical_environments: dict[
            ast.ClassDef, dict[str, set[_FacadeAnalysis._Rule]]
        ] = {}
        self.visit(tree)
        self._module_environment = self._copy_environment(self.environment)

    @staticmethod
    def _copy_environment(
        environment: Mapping[str, set[_Rule]],
    ) -> dict[str, set[_Rule]]:
        return {name: set(rules) for name, rules in environment.items()}

    @staticmethod
    def _merge_environments(
        *environments: Mapping[str, set[_Rule]],
    ) -> dict[str, set[_Rule]]:
        if not environments:
            return {}
        names = set.intersection(*(set(environment) for environment in environments))
        return {
            name: set(environments[0][name])
            for name in names
            if all(
                environment[name] == environments[0][name]
                for environment in environments[1:]
            )
        }

    def _remember_roots(self, name: str, rules: Iterable[_Rule]) -> None:
        for match, _capability in rules:
            self._roots.add(".".join((name, *match)))

    def _set_rules(self, name: str, rules: Iterable[_Rule]) -> None:
        values = set(rules)
        if values:
            self.environment[name] = values
            self._remember_roots(name, values)
        else:
            self.environment.pop(name, None)

    def _resolve_with(
        self, node: ast.AST,
        environment: Mapping[str, set[_Rule]] | None = None,
    ) -> tuple[str, ...] | None:
        chain = _attribute_chain(node)
        if chain is None:
            return None
        root, raw_attributes = chain
        attributes = tuple(raw_attributes)
        candidates = {
            (*capability, *attributes[len(match):])
            for match, capability in (environment or self.environment).get(root, set())
            if attributes[:len(match)] == match
        }
        if len(candidates) != 1:
            return None
        return next(iter(candidates))

    def _rules_for(self, node: ast.AST) -> set[_Rule]:
        chain = _attribute_chain(node)
        if chain is None:
            return set()
        root, raw_attributes = chain
        attributes = tuple(raw_attributes)
        result: set[_FacadeAnalysis._Rule] = set()
        for match, capability in self.environment.get(root, set()):
            if attributes[:len(match)] == match:
                result.add(((), (*capability, *attributes[len(match):])))
            elif match[:len(attributes)] == attributes:
                result.add((match[len(attributes):], capability))
        return result

    def _record(self, node: ast.AST) -> None:
        capability = self._resolve_with(node)
        if capability is not None:
            self.resolved[node] = capability

    def visit_Name(self, node: ast.Name) -> None:
        self._record(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._record(node)
        self.generic_visit(node)

    def visit_Module(self, node: ast.Module) -> None:
        for statement in node.body:
            self.visit(statement)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "rag":
                self._set_rules(alias.asname or "rag", {((), ())})
                continue
            exports = self.exposed_bindings.get(alias.name, {})
            if alias.asname:
                binding = alias.asname
                module_prefix: tuple[str, ...] = ()
            else:
                parts = tuple(alias.name.split("."))
                binding = parts[0]
                module_prefix = parts[1:]
            self._set_rules(
                binding,
                {
                    ((*module_prefix, exported), capability)
                    for exported, capability in exports.items()
                },
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            for alias in node.names:
                if alias.name != "*":
                    self._set_rules(alias.asname or alias.name, set())
            return
        if node.module == "rag":
            for alias in node.names:
                if alias.name != "*":
                    self._set_rules(
                        alias.asname or alias.name,
                        {((), tuple(alias.name.split(".")))},
                    )
            return
        exports = self.exposed_bindings.get(node.module or "", {})
        for alias in node.names:
            if alias.name == "*":
                for binding, capability in exports.items():
                    self._set_rules(binding, {((), capability)})
            else:
                capability = exports.get(alias.name)
                self._set_rules(
                    alias.asname or alias.name,
                    set() if capability is None else {((), capability)},
                )

    @staticmethod
    def _assignment_pairs(
        target: ast.AST, value: ast.AST,
    ) -> list[tuple[ast.AST, ast.AST]]:
        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            return list(zip(target.elts, value.elts))
        return [(target, value)]

    def _bind_target(self, target: ast.AST, rules: set[_Rule]) -> None:
        if isinstance(target, ast.Name):
            self._set_rules(target.id, rules)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._bind_target(item, set())
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, set())

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
            for nested_target, nested_value in self._assignment_pairs(
                target, node.value
            ):
                self._bind_target(nested_target, self._rules_for(nested_value))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is None:
            return
        self.visit(node.value)
        self.visit(node.target)
        self._bind_target(node.target, self._rules_for(node.value))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.visit(node.target)
        self._bind_target(node.target, self._rules_for(node.value))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._set_rules(node.target.id, set())

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.visit(target)
            if isinstance(target, ast.Name):
                self._set_rules(target.id, set())

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

    def _scope_bindings(self, scope: ast.AST) -> tuple[set[str], set[str]]:
        bound = {argument.arg for argument in self._arguments(scope)}
        globals_: set[str] = set()
        nonlocals: set[str] = set()
        for node, owner in self.scope_map.node_scope.items():
            if owner is not scope:
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, ast.Import):
                bound.update(
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                bound.update(
                    alias.asname or alias.name
                    for alias in node.names if alias.name != "*"
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Global):
                globals_.update(node.names)
            elif isinstance(node, ast.Nonlocal):
                nonlocals.update(node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
        return bound - globals_ - nonlocals, globals_

    def _function_header(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        for argument in self._arguments(node):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        for default in [
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
        ]:
            self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self._function_header(node)
        outer = self.environment
        parent = self.scope_map.parent_scope.get(node)
        lexical = (
            self._class_lexical_environments[parent]
            if isinstance(parent, ast.ClassDef)
            else outer
        )
        local = self._copy_environment(lexical)
        bound, _globals = self._scope_bindings(node)
        for name in bound:
            local.pop(name, None)
        self.environment = local
        for statement in node.body:
            self.visit(statement)
        self.environment = outer
        self._set_rules(node.name, set())

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
        ]:
            self.visit(default)
        outer = self.environment
        parent = self.scope_map.parent_scope.get(node)
        lexical = (
            self._class_lexical_environments[parent]
            if isinstance(parent, ast.ClassDef)
            else outer
        )
        local = self._copy_environment(lexical)
        bound, _globals = self._scope_bindings(node)
        for name in bound:
            local.pop(name, None)
        self.environment = local
        self.visit(node.body)
        self.environment = outer

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        for expression in [*node.bases, *(keyword.value for keyword in node.keywords)]:
            self.visit(expression)
        outer = self.environment
        parent = self.scope_map.parent_scope.get(node)
        self._class_lexical_environments[node] = self._copy_environment(
            self._class_lexical_environments[parent]
            if isinstance(parent, ast.ClassDef)
            else outer
        )
        self.environment = self._copy_environment(outer)
        for statement in node.body:
            self.visit(statement)
        self.environment = outer
        self._set_rules(node.name, set())

    def _visit_branch(
        self, statements: Iterable[ast.AST],
        starting: Mapping[str, set[_Rule]],
    ) -> dict[str, set[_Rule]]:
        outer = self.environment
        self.environment = self._copy_environment(starting)
        for statement in statements:
            self.visit(statement)
        result = self.environment
        self.environment = outer
        return result

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        starting = self._copy_environment(self.environment)
        body = self._visit_branch(node.body, starting)
        otherwise = self._visit_branch(node.orelse, starting)
        self.environment = self._merge_environments(body, otherwise)

    def _visit_loop(
        self, header: Iterable[ast.AST], target: ast.AST | None,
        body: Iterable[ast.AST],
    ) -> None:
        for expression in header:
            self.visit(expression)
        starting = self._copy_environment(self.environment)
        outer = self.environment
        self.environment = self._copy_environment(starting)
        if target is not None:
            self.visit(target)
            self._bind_target(target, set())
        for statement in body:
            self.visit(statement)
        executed = self.environment
        self.environment = outer
        self.environment = self._merge_environments(starting, executed)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop((node.iter,), node.target, (*node.body, *node.orelse))

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop((node.iter,), node.target, (*node.body, *node.orelse))

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop((node.test,), None, (*node.body, *node.orelse))

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
                self._bind_target(item.optional_vars, set())
        for statement in node.body:
            self.visit(statement)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        starting = self._copy_environment(self.environment)
        branches = [self._visit_branch(node.body, starting)]
        for handler in node.handlers:
            handler_nodes: list[ast.AST] = []
            if handler.type is not None:
                handler_nodes.append(handler.type)
            handler_nodes.extend(handler.body)
            branches.append(self._visit_branch(handler_nodes, starting))
        merged = self._merge_environments(*branches)
        after_else = self._visit_branch(node.orelse, merged)
        self.environment = self._visit_branch(node.finalbody, after_else)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        starting = self._copy_environment(self.environment)
        branches = [starting]
        for case in node.cases:
            statements: list[ast.AST] = []
            if case.guard is not None:
                statements.append(case.guard)
            statements.extend(case.body)
            branches.append(self._visit_branch(statements, starting))
        self.environment = self._merge_environments(*branches)

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        results: Iterable[ast.AST],
    ) -> None:
        first, *remaining = node.generators
        self.visit(first.iter)
        outer = self.environment
        self.environment = self._copy_environment(outer)
        self.visit(first.target)
        self._bind_target(first.target, set())
        for condition in first.ifs:
            self.visit(condition)
        for generator in remaining:
            self.visit(generator.iter)
            self.visit(generator.target)
            self._bind_target(generator.target, set())
            for condition in generator.ifs:
                self.visit(condition)
        for result in results:
            self.visit(result)
        self.environment = outer

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def resolve(self, node: ast.AST) -> list[str] | None:
        capability = self.resolved.get(node)
        return None if capability is None else list(capability)

    def roots(self) -> list[str]:
        return sorted(self._roots)

    def module_exports(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, tuple[str, ...]] = {}
        for name, rules in sorted(self._module_environment.items()):
            direct = {capability for match, capability in rules if not match}
            if len(direct) == 1:
                result[name] = next(iter(direct))
        return result


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
    exposed_bindings: Mapping[str, Mapping[str, tuple[str, ...]]],
) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("rag."):
        return value[4:]
    for module, bindings in sorted(exposed_bindings.items()):
        for binding, capability in sorted(bindings.items()):
            prefix = f"{module}.{binding}."
            if value.startswith(prefix):
                remainder = tuple(value[len(prefix):].split("."))
                return ".".join((*capability, *remainder))
    return None


def _patch_target(
    call: ast.Call,
    resolver: _FacadeAnalysis,
    exposed_bindings: Mapping[str, Mapping[str, tuple[str, ...]]],
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
    exposed_bindings: Mapping[str, Mapping[str, tuple[str, ...]]],
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
    exposed_bindings: Mapping[str, Mapping[str, tuple[str, ...]]],
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
    exposed_bindings: Mapping[str, Mapping[str, tuple[str, ...]]],
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
) -> dict[str, dict[str, tuple[str, ...]]]:
    exports: dict[str, dict[str, tuple[str, ...]]] = {
        module: {} for module in modules
    }
    changed = True
    while changed:
        changed = False
        for module, path in sorted(modules.items()):
            if module == "rag":
                continue
            analysis = _FacadeAnalysis(trees[path], exports)
            observed = analysis.module_exports()
            if any(exports[module].get(name) != capability
                   for name, capability in observed.items()):
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
        probe_path = isolated_root / "architecture_runtime_probe.py"
        probe_path.write_text(_RUNTIME_PROBE, encoding="utf-8", newline="\n")
        child_environment_name = "RAG_ARCH_INVENTORY_SUPERVISED_CHILD"
        run_id_environment_name = "RAG_ARCH_INVENTORY_RUN_ID"
        overrides: dict[str, str | None] = {
            name: None
            for name in os.environ
            if name.casefold() != child_environment_name.casefold()
        }
        overrides.update(environment)
        config = process_supervision.SupervisionConfig(
            supervised_child_env=child_environment_name,
            run_id_env=run_id_environment_name,
            terminate_grace=5.0,
            poll_interval=0.05,
            start_gate_timeout=10.0,
        )
        try:
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                def enforce_output_ceiling(_process: object) -> None:
                    if any(
                        os.fstat(handle.fileno()).st_size > 4 * 1024 * 1024
                        for handle in (stdout_file, stderr_file)
                    ):
                        raise ArchitectureInventoryError(
                            "Isolated rag runtime probe exceeded its output ceiling"
                        )

                return_code = process_supervision._run_cli_with_deadline(
                    probe_path,
                    [str(root)],
                    operation="architecture runtime probe",
                    timeout=90.0,
                    config=config,
                    working_directory=isolated_root,
                    environment_overrides=overrides,
                    heartbeat=enforce_output_ceiling,
                    stdout_target=stdout_file,
                    stderr_target=stderr_file,
                    warn_fn=lambda _message: None,
                )
                enforce_output_ceiling(None)
                stdout_file.flush()
                stderr_file.flush()
        except ArchitectureInventoryError:
            raise
        except process_supervision._SupervisorCleanupError as exc:
            raise ArchitectureInventoryError(
                "Isolated rag runtime probe cleanup could not be confirmed"
            ) from exc
        except BaseException as exc:
            raise ArchitectureInventoryError(
                "Isolated rag runtime probe failed (supervision error)"
            ) from exc
        if return_code == 124:
            raise ArchitectureInventoryError(
                "Isolated rag runtime probe timed out"
            )
        if return_code != 0:
            exit_kind = "signal" if return_code > 128 else "nonzero exit"
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


def _compact_runtime_contract(
    value: Mapping[str, object],
) -> dict[str, object]:
    compact = dict(value)
    compact["callables"] = [
        {
            "contract_sha256": _domain_sha256(
                HASH_DOMAINS["runtime_symbol_contract"], item
            ),
            "kind": item["kind"],
            "module": item["module"],
            "name": item["name"],
            "qualname": item["qualname"],
        }
        for item in value["callables"]
    ]
    return compact


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
    module_records: list[dict[str, object]] = []
    for module, relative in sorted(modules.items()):
        tree = trees[relative]
        edges = _module_import_edges(module, relative, tree, known)
        graph[module].update(edges)
        raw_functions, raw_classes = _definitions(tree)
        functions, classes, definition_locations_sha256 = _compact_definitions(
            module, raw_functions, raw_classes
        )
        source = sources[relative]
        module_records.append({
            "class_count": len(classes),
            "classes": classes,
            "definition_locations_sha256": definition_locations_sha256,
            "function_count": len(functions),
            "functions": functions,
            "import_edges": [
                {
                    "contexts": sorted({context for context, _origin in evidence}),
                    "origins": sorted({origin for _context, origin in evidence}),
                    "target": target,
                }
                for target, evidence in sorted(edges.items())
            ],
            "module": module,
            "non_blank_lines": sum(bool(line.strip()) for line in source.splitlines()),
            "path": relative.as_posix(),
            "physical_lines": len(source.splitlines()),
            "top_level_function_count": sum(
                item["scope"] == "top_level" for item in raw_functions
            ),
        })

    components = _strongly_connected_components(graph)
    cyclic = [
        component
        for component in components
        if len(component) > 1 or component[0] in graph[component[0]]
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
        "hash_domains": dict(HASH_DOMAINS),
        "source_architecture": {
            "class_count": sum(len(item["classes"]) for item in module_records),
            "function_count": sum(len(item["functions"]) for item in module_records),
            "import_graph": {
                "cyclic_components": cyclic,
                "edge_policy": "first-party-all-contexts-with-provenance-v1",
                "edge_count": sum(len(dependencies) for dependencies in graph.values()),
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
        "rag_runtime_contract": _compact_runtime_contract(
            _runtime_facade_contract(resolved_root)
        ),
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
    keys = {"name", "scope"}
    if function:
        keys.update({"kind", "signature_sha256"})
    record = _require_dict(value, keys, context)
    _require_string(record["name"], f"{context}.name")
    if record["scope"] not in {"top_level", "class", "nested"}:
        raise ArchitectureInventoryError(f"{context}.scope is invalid")
    if function:
        if record["kind"] not in {"function", "async_function"}:
            raise ArchitectureInventoryError(f"{context}.kind is invalid")
        _validate_sha256(
            record["signature_sha256"], f"{context}.signature_sha256"
        )


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


def _validate_edge_records_v3(
    value: object, context: str, known_modules: set[str],
) -> list[dict[str, Any]]:
    raw_edges = _require_list(value, context)
    edges: list[dict[str, Any]] = []
    targets: list[str] = []
    for index, raw_edge in enumerate(raw_edges):
        label = f"{context}[{index}]"
        edge = _require_dict(
            raw_edge, {"contexts", "origins", "target"}, label
        )
        target = _require_string(edge["target"], f"{label}.target")
        if target not in known_modules:
            raise ArchitectureInventoryError(f"{label}.target is not architectural")
        contexts = _validate_sorted_strings(
            edge["contexts"], f"{label}.contexts"
        )
        origins = _validate_sorted_strings(edge["origins"], f"{label}.origins")
        if not contexts or not set(contexts) <= set(_EDGE_CONTEXTS):
            raise ArchitectureInventoryError(f"{label}.contexts is invalid")
        if not origins or not set(origins) <= set(_EDGE_ORIGINS):
            raise ArchitectureInventoryError(f"{label}.origins is invalid")
        edges.append(edge)
        targets.append(target)
    if targets != sorted(set(targets)):
        raise ArchitectureInventoryError(f"{context} must be target-sorted and unique")
    return edges


def _validate_source_architecture_v3(
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
            "class_count", "classes", "definition_locations_sha256",
            "function_count", "functions", "import_edges", "module",
            "non_blank_lines", "path", "physical_lines",
            "top_level_function_count",
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
        _validate_sha256(
            module["definition_locations_sha256"],
            f"{label}.definition_locations_sha256",
        )
        functions = _require_list(module["functions"], f"{label}.functions")
        function_keys: list[tuple[str, str]] = []
        for def_index, definition in enumerate(functions):
            def_label = f"{label}.functions[{def_index}]"
            _validate_definition(definition, def_label, function=True)
            function_keys.append((definition["name"], definition["kind"]))
        if function_keys != sorted(function_keys):
            raise ArchitectureInventoryError(
                f"{label}.functions is not name-sorted"
            )
        classes = _require_list(module["classes"], f"{label}.classes")
        class_keys: list[str] = []
        for def_index, definition in enumerate(classes):
            def_label = f"{label}.classes[{def_index}]"
            _validate_definition(definition, def_label, function=False)
            class_keys.append(definition["name"])
        if class_keys != sorted(class_keys):
            raise ArchitectureInventoryError(
                f"{label}.classes is not name-sorted"
            )
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
        edges = _validate_edge_records_v3(
            module["import_edges"], f"{label}.import_edges", known_modules
        )
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
        "cyclic_components", "edge_count", "edge_policy",
    }, f"{context}.import_graph")
    if graph["edge_policy"] != "first-party-all-contexts-with-provenance-v1":
        raise ArchitectureInventoryError(
            f"{context}.import_graph.edge_policy is invalid"
        )
    if _require_int(graph["edge_count"], f"{context}.import_graph.edge_count") != sum(
        len(edges) for edges in graph_sets.values()
    ):
        raise ArchitectureInventoryError(f"{context}.import_graph.edge_count is inconsistent")
    components = _strongly_connected_components(graph_sets)
    cyclic = [
        component for component in components
        if len(component) > 1 or component[0] in graph_sets[component[0]]
    ]
    raw_cyclic = _require_list(
        graph["cyclic_components"], f"{context}.import_graph.cyclic_components"
    )
    for index, raw_component in enumerate(raw_cyclic):
        component = _validate_sorted_strings(
            raw_component,
            f"{context}.import_graph.cyclic_components[{index}]",
        )
        if not component or not set(component) <= known_modules:
            raise ArchitectureInventoryError(
                f"{context}.import_graph.cyclic_components[{index}] is invalid"
            )
    if raw_cyclic != cyclic:
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


def _validate_runtime_contract_v3(value: object) -> None:
    context = "rag_runtime_contract"
    contract = _require_dict(value, {
        "behavior", "callables", "dir_names", "import_star_names",
        "interpreter_variant_exclusions", "logger_name", "module_type",
        "platform_variant_bindings", "vars_names",
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
    platform_variants = _require_list(
        contract["platform_variant_bindings"],
        f"{context}.platform_variant_bindings",
    )
    expected_platform_variants = [
        {"name": name, "token": token}
        for name, token in sorted(_PLATFORM_VARIANT_BINDINGS.items())
    ]
    for index, raw_variant in enumerate(platform_variants):
        label = f"{context}.platform_variant_bindings[{index}]"
        variant = _require_dict(raw_variant, {"name", "token"}, label)
        _require_string(variant["name"], f"{label}.name")
        _require_string(variant["token"], f"{label}.token")
    if any(variant not in expected_platform_variants for variant in platform_variants):
        raise ArchitectureInventoryError(
            f"{context}.platform_variant_bindings is invalid"
        )
    callables = _require_list(contract["callables"], f"{context}.callables")
    callable_names: list[str] = []
    for index, raw_callable in enumerate(callables):
        label = f"{context}.callables[{index}]"
        item = _require_dict(raw_callable, {
            "contract_sha256", "kind", "module", "name", "qualname",
        }, label)
        if item["kind"] not in {"class", "function"}:
            raise ArchitectureInventoryError(f"{label}.kind is invalid")
        name = _require_string(item["name"], f"{label}.name")
        _require_string(item["module"], f"{label}.module")
        _require_string(item["qualname"], f"{label}.qualname")
        _validate_sha256(item["contract_sha256"], f"{label}.contract_sha256")
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
    """Validate schema v3 and all content-free cross-field invariants."""
    document = _require_dict(value, {
        "analysis_model", "consumers", "interpreter_normalization",
        "hash_domains", "rag_runtime_contract", "rag_source_owner",
        "schema_version", "source_architecture", "tracked_sources",
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
    hash_domains = _require_dict(
        document["hash_domains"], set(HASH_DOMAINS), "inventory.hash_domains"
    )
    if hash_domains != HASH_DOMAINS:
        raise ArchitectureInventoryError("inventory.hash_domains is unsupported")
    grouped_paths, tracked_paths = _validate_tracked_sources_v2(
        document["tracked_sources"]
    )
    known_modules = _validate_source_architecture_v3(
        document["source_architecture"], grouped_paths
    )
    _validate_rag_source_owner_v2(
        document["rag_source_owner"], grouped_paths, known_modules
    )
    _validate_runtime_contract_v3(document["rag_runtime_contract"])
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
