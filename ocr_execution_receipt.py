"""Content-free provenance collected inside actual OCR execution processes.

Receipts bind observations and installation evidence; they are not signed remote
attestations. Model files are reverified at observation, but a path-loaded ONNX
session is not an immutable byte-to-session attestation.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
from pathlib import Path
import re
import sys
import time

from evaluation_inputs import _hex_digest, _read_snapshot, _strict_json_bytes
from ocr_experiment_runtime import _name as normalize_package, capture_runtime_manifest
from ocr_disposition_observer import DispatchBinding


ROOT = Path(__file__).resolve().parent
_ROLES = {"detection": "text_det", "classification": "text_cls", "recognition": "text_rec"}
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}")
_SCOPE = "Approved package model files verified before load and at observation; not immutable session-byte attestation."
_INSTALL_STEPS = {"create_environment", "bootstrap", "verify_installer", "sync", "check", "inventory"}
_INSTALL_SCOPE = "Installer execution and dependency consistency; not installed wheel, build-environment, or native-library byte attestation."


class ExecutionRecorder:
    """Record raw dispatch for known guarded engines; callable time otherwise.

    Guard setup/teardown is excluded. A completed raw dispatch can still produce
    a rejected candidate when post-dispatch guard or reader validation fails.
    Explicit disposition capture requires the fixed guard topology; generic
    callables cannot supply its raw-dispatch proof and are refused before use.
    """

    def __init__(self, *, disposition_binding=None):
        if disposition_binding is not None and type(disposition_binding) is not DispatchBinding:
            raise ValueError("execution disposition binding requires fixed internal ownership")
        self.disposition_binding = disposition_binding
        self.calls = []
        self.observation = None

    def reader_factory(self, reader_type):
        from ocr_engine_guard import RapidOCREngineGuard

        recorder = self

        class TimedEngine:
            def __init__(self, engine, reader):
                self.engine = engine
                self._disposition_reader = reader if recorder.disposition_binding is not None else None

            def __getattr__(self, name):
                return getattr(self.engine, name)

            def _observe(self, dispatch, ticket=None):
                started, status = time.monotonic(), "failed"
                try:
                    result = dispatch()
                    status = "completed"
                    return result
                finally:
                    recorder.calls.append({"id": f"call-{len(recorder.calls) + 1:04d}", "status": status,
                                           "elapsed_seconds": time.monotonic() - started})
                    # Fixed primitive commitment only: no diagnostic callback
                    # may turn a returned raw call into a failed receipt.
                    if ticket is not None:
                        ticket.raw_status = status

            def __call__(self, *args, **kwargs):
                # Check before guard preparation/hooks, not inside the observer.
                if len(recorder.calls) >= 100:
                    raise ValueError("execution exceeds observed OCR call budget")
                if type(self.engine) is RapidOCREngineGuard:
                    if recorder.disposition_binding is not None:
                        ticket = recorder.disposition_binding.reserve(recorder, self._disposition_reader, self.engine, len(recorder.calls) + 1)
                        if ticket is None:
                            raise ValueError("disposition capture requires its reserved dispatch ticket")
                        return self.engine.invoke_observed(lambda dispatch: self._observe(dispatch, ticket),
                            *args, disposition_frame=ticket.frame, **kwargs)
                    return self.engine.invoke_observed(self._observe, *args, **kwargs)
                if recorder.disposition_binding is not None:
                    raise ValueError("disposition capture requires a guarded OCR engine")
                # Never duck-type arbitrary observer attributes as trusted guards.
                return self._observe(lambda: self.engine(*args, **kwargs))

        class RecordedReader(reader_type):
            def _load_engine(self):
                engine = super()._load_engine()
                if not isinstance(engine, TimedEngine):
                    if recorder.disposition_binding is not None and type(engine) is not RapidOCREngineGuard:
                        raise ValueError("disposition capture requires a guarded OCR engine")
                    self._engine = TimedEngine(engine, self)
                return self._engine

            def close(self):
                try:
                    if self._engine is not None:
                        recorder.observation = self.execution_observation()
                finally:
                    super().close()

        return RecordedReader


def environment_identity(path: Path) -> str:
    """Bind a local interpreter prefix without publishing its pathname."""
    return hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode("utf-8")).hexdigest()


def _safe(value: object, pattern=_SAFE) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError("execution receipt identifier is invalid")
    return value


def _object(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("execution observation has invalid fields")
    return value


def _elapsed(value: object) -> float:
    if type(value) not in (int, float) or not 0 <= value <= 86400 or not math.isfinite(value):
        raise ValueError("execution duration is outside bounds")
    return float(value)


def _loaded_source_digests() -> dict[str, str]:
    result = {}
    for name, module in list(sys.modules.items()):
        path = vars(module).get("__file__") if module is not None else None
        if not isinstance(path, str) or not path.endswith(".py"):
            continue
        path = Path(path).absolute()
        if path.parent not in (ROOT, ROOT / "tools"):
            continue
        key = "entrypoint" if name == "__main__" else name
        if _SAFE.fullmatch(key) is None:
            continue
        result[key] = _read_snapshot(path, label="executed project source", max_bytes=4 * 1024 * 1024)[1]
        if len(result) > 256:
            raise ValueError("executed project source inventory exceeds bounds")
    return dict(sorted(result.items()))


def validate_engine_observation(payload: object) -> dict:
    value = _object(payload, {"engine", "sessions", "model_artifacts", "verification_scope"})
    engine = _object(value["engine"], {"name", "version"})
    if engine["name"] != "rapidocr" or value["verification_scope"] != _SCOPE:
        raise ValueError("execution observation has an unsupported engine or scope")
    engine = {"name": "rapidocr", "version": _safe(engine["version"], _VERSION)}
    if not isinstance(value["model_artifacts"], list) or len(value["model_artifacts"]) != 3:
        raise ValueError("execution requires all three approved model identities")
    models = {}
    for raw in value["model_artifacts"]:
        model = dict(_object(raw, {"id", "sha256", "bytes"}))
        role = _safe(model["id"])
        if role not in _ROLES or role in models:
            raise ValueError("execution model roles are invalid")
        _hex_digest(model["sha256"], label="execution model digest")
        if type(model["bytes"]) is not int or not 1 <= model["bytes"] <= 64 * 1024 * 1024:
            raise ValueError("execution model size is outside bounds")
        models[role] = model
    if not isinstance(value["sessions"], list) or len(value["sessions"]) != 3:
        raise ValueError("execution requires all three constructed sessions")
    sessions = {}
    for raw in value["sessions"]:
        session = _object(raw, {"role", "execution_providers", "thread_settings", "model_sha256"})
        role = _safe(session["role"])
        providers = session["execution_providers"]
        if role not in models or role in sessions or session["model_sha256"] != models[role]["sha256"]:
            raise ValueError("execution session model binding is invalid")
        if not isinstance(providers, list) or not 1 <= len(providers) <= 16:
            raise ValueError("execution providers are outside bounds")
        providers = [_safe(item) for item in providers]
        if len(providers) != len(set(providers)):
            raise ValueError("execution providers are duplicated")
        threads = _object(session["thread_settings"], {"intra_op", "inter_op"})
        if any(type(item) is not int or not 0 <= item <= 256 for item in threads.values()):
            raise ValueError("execution session options are outside bounds")
        sessions[role] = {"role": role, "execution_providers": providers, "thread_settings": dict(threads),
                          "model_sha256": models[role]["sha256"]}
    return {"engine": engine, "sessions": [sessions[key] for key in sorted(sessions)],
            "model_artifacts": [models[key] for key in sorted(models)], "verification_scope": _SCOPE}


def observe_rapidocr_engine(engine, *, engine_version: str, model_paths: dict) -> dict:
    """Inspect actual constructed sessions; verify approved model files again."""
    from model_artifacts import package_model, verified_installed_package_model

    artifact = package_model("rapidocr")
    if artifact is None or artifact.version != engine_version:
        raise ValueError("execution engine version differs from approved package")
    verified = verified_installed_package_model("rapidocr", "docling_ocr")
    if (set(model_paths) != set(verified)
            or any(Path(model_paths[role]).absolute() != Path(path).absolute() for role, path in verified.items())):
        raise ValueError("execution model paths differ from verified load paths")
    models = [{"id": item.role, "sha256": item.content_sha256, "bytes": item.size} for item in artifact.files]
    digests = {item["id"]: item["sha256"] for item in models}
    sessions = []
    try:
        for role, attribute in sorted(_ROLES.items()):
            session = getattr(engine, attribute).session.session
            options = session.get_session_options()
            sessions.append({"role": role, "execution_providers": list(session.get_providers()),
                             "thread_settings": {"intra_op": options.intra_op_num_threads,
                                                 "inter_op": options.inter_op_num_threads},
                             "model_sha256": digests[role]})
    except Exception:
        raise ValueError("actual OCR session observation is unavailable") from None
    return validate_engine_observation({"engine": {"name": "rapidocr", "version": engine_version},
                                        "sessions": sessions, "model_artifacts": models,
                                        "verification_scope": _SCOPE})


def validate_installation_evidence(path: Path) -> tuple[dict, str]:
    """Recheck a completed installer receipt and all bounded private step logs.

    This verifies local consistency with the executing environment, not who
    authored an editable receipt or the installed wheel/native-library bytes.
    """
    raw, digest = _read_snapshot(Path(path), label="installation receipt", max_bytes=64 * 1024)
    value = _strict_json_bytes(raw, label="installation receipt", max_bytes=64 * 1024)
    required = {"schema_version", "kind", "recipe", "installer_source_sha256", "locks", "steps", "status",
                "installed_under_hash_locks", "scope", "environment_identity_sha256", "python_executable_sha256"}
    _object(value, required)
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "ocr_locked_environment_installation"
            or value["recipe"] != "repository-full-cpu-hash-sync-v1"
            or value["status"] != "complete" or value["installed_under_hash_locks"] is not True
            or value["scope"] != _INSTALL_SCOPE):
        raise ValueError("installation receipt is not a completed locked installation")
    if value["environment_identity_sha256"] != environment_identity(Path(sys.prefix)):
        raise ValueError("installation receipt belongs to a different environment")
    _, executable_digest = _read_snapshot(Path(sys.executable), label="execution interpreter", max_bytes=64 * 1024 * 1024)
    if value["python_executable_sha256"] != executable_digest:
        raise ValueError("installation interpreter changed")
    _hex_digest(value["installer_source_sha256"], label="installer source digest")
    if _read_snapshot(Path(path).parent / "installer-source.py", label="installer source snapshot", max_bytes=1024 * 1024)[1] != value["installer_source_sha256"]:
        raise ValueError("installer source snapshot changed")
    locks = _object(value["locks"], {"requirements-full.lock", "requirements-test.lock", "requirements-lock-tools.lock"})
    for name, expected in locks.items():
        _hex_digest(expected, label="installation lock digest")
        if _read_snapshot(ROOT / name, label="execution lock", max_bytes=8 * 1024 * 1024)[1] != expected:
            raise ValueError("installation lock differs from current execution lock")
    _object(value["steps"], _INSTALL_STEPS)
    inventory = None
    for name, step in value["steps"].items():
        _object(step, {"status", "exit_code", "elapsed_seconds", "log_sha256", "log_bytes"})
        if step["status"] != "complete" or type(step["exit_code"]) is not int or step["exit_code"] != 0:
            raise ValueError("installation contains a failed or incomplete step")
        _elapsed(step["elapsed_seconds"])
        if type(step["log_bytes"]) is not int or not 0 <= step["log_bytes"] <= 16 * 1024 * 1024:
            raise ValueError("installation log exceeds bounds")
        _hex_digest(step["log_sha256"], label="installation log digest")
        log, actual = _read_snapshot(Path(path).parent / (name + ".log"), label="installation log", max_bytes=16 * 1024 * 1024)
        if actual != step["log_sha256"] or len(log) != step["log_bytes"]:
            raise ValueError("installation evidence log changed")
        if name == "inventory":
            inventory = _strict_json_bytes(log, label="installed package inventory", max_bytes=1024 * 1024)
    _object(inventory, {"python_version", "packages"})
    if inventory["python_version"] != platform.python_version():
        raise ValueError("installation Python version differs from execution")
    if not isinstance(inventory["packages"], list) or not 1 <= len(inventory["packages"]) <= 512:
        raise ValueError("installation package inventory exceeds bounds")
    expected = {}
    for item in inventory["packages"]:
        _object(item, {"name", "version"})
        name = normalize_package(item["name"])
        if name in expected:
            raise ValueError("installation package inventory contains duplicates")
        expected[name] = _safe(item["version"], _VERSION)
    actual = {}
    for distribution in importlib.metadata.distributions():
        name = normalize_package(distribution.metadata["Name"])
        if name in actual:
            raise ValueError("execution package inventory contains duplicates")
        actual[name] = _safe(distribution.version, _VERSION)
    if actual != expected:
        raise ValueError("installed package set changed after qualified synchronization")
    return value, digest


def capture_execution_receipt(*, operation: str, inputs: dict[str, str], output_sha256: str,
                              observation: object, calls: list[dict], elapsed_seconds: float,
                              installation_path: Path | None = None) -> dict:
    """Bind actual in-process observations to a completed/partial experiment.

    Callers must collect calls around real engine invocations and use source
    snapshot/commit protection. This helper does not run OCR or publish files.
    """
    operation = _safe(operation)
    if not isinstance(inputs, dict) or not 1 <= len(inputs) <= 16:
        raise ValueError("execution input bindings exceed bounds")
    inputs = {_safe(key): _hex_digest(value, label="execution input digest") for key, value in inputs.items()}
    _hex_digest(output_sha256, label="execution output digest")
    elapsed = _elapsed(elapsed_seconds)
    if not isinstance(calls, list) or len(calls) > 100:
        raise ValueError("execution call count exceeds bounds")
    checked = []
    for call in calls:
        call = _object(call, {"id", "status", "elapsed_seconds"})
        if call["status"] not in ("completed", "failed"):
            raise ValueError("execution call status is invalid")
        checked.append({"id": _safe(call["id"]), "status": call["status"], "elapsed_seconds": _elapsed(call["elapsed_seconds"])})
    if len({call["id"] for call in checked}) != len(checked):
        raise ValueError("execution call IDs must be unique")
    if sum(call["elapsed_seconds"] for call in checked) > elapsed + 1e-6:
        raise ValueError("sequential OCR call durations exceed the experiment duration")
    observed = validate_engine_observation(observation) if observation is not None else None
    if any(call["status"] == "completed" for call in checked) and observed is None:
        raise ValueError("completed OCR calls require actual session evidence")
    effective = None
    if observed is not None:
        sessions = observed["sessions"]
        uniform_threads = all(item["thread_settings"] == sessions[0]["thread_settings"] for item in sessions)
        effective = {"engine": observed["engine"], "execution_providers": sorted({p for item in sessions for p in item["execution_providers"]}),
                     "thread_settings": sessions[0]["thread_settings"] if uniform_threads else {"intra_op": None, "inter_op": None},
                     "elapsed_seconds": elapsed, "model_artifacts": [{"id": item["id"], "sha256": item["sha256"]} for item in observed["model_artifacts"]]}
    runtime = capture_runtime_manifest(ROOT / "requirements-full.lock", effective_runtime=effective)
    installation, installation_digest = (validate_installation_evidence(installation_path) if installation_path is not None else (None, None))
    completed = sum(call["status"] == "completed" for call in checked)
    return {"schema_version": 1, "kind": "ocr_execution_receipt", "operation": operation,
            "inputs": dict(sorted(inputs.items())), "output_sha256": output_sha256,
            "environment_identity_sha256": environment_identity(Path(sys.prefix)),
            "installation_sha256": installation_digest,
            "installation_evidence": "verified_local_bindings" if installation is not None else "not_supplied",
            "project_sources_at_observation": _loaded_source_digests(),
            "runtime": runtime, "engine_observation": observed, "calls": checked,
            "summary": {"attempted": len(checked), "completed": completed, "failed": len(checked) - completed},
            "elapsed_seconds": elapsed, "ocr_executed": bool(checked),
            "requires_attention": (not checked or completed != len(checked) or installation is None or not runtime["package_versions_match"]),
            "scope": "Local execution and evidence bindings, not signed artifact attestation or accuracy certification. Source hashes describe disk snapshots at observation, not loaded-byte attestation. Constructed sessions do not prove every role executed."}


def validate_execution_receipt(payload: object, *, installation_path: Path | None = None) -> dict:
    """Strict local readback; recompute summaries and compare runtime metadata.

    Loaded version strings remain child observations, not parent imports or
    authenticated binary evidence. They must agree with their reported pins.
    """
    keys = {"schema_version", "kind", "operation", "inputs", "output_sha256", "environment_identity_sha256",
            "installation_sha256", "installation_evidence", "project_sources_at_observation", "runtime",
            "engine_observation", "calls", "summary", "elapsed_seconds", "ocr_executed", "requires_attention", "scope"}
    value = _object(payload, keys)
    expected = capture_execution_receipt(operation=value["operation"], inputs=value["inputs"],
                                         output_sha256=value["output_sha256"], observation=value["engine_observation"],
                                         calls=value["calls"], elapsed_seconds=value["elapsed_seconds"],
                                         installation_path=installation_path)
    if expected["engine_observation"] is not None:
        from model_artifacts import package_model, verified_installed_package_model
        artifact = package_model("rapidocr")
        observed = expected["engine_observation"]
        approved = sorted([{"id": item.role, "sha256": item.content_sha256, "bytes": item.size}
                           for item in artifact.files], key=lambda item: item["id"]) if artifact else None
        if (artifact is None or observed["engine"]["version"] != artifact.version
                or observed["model_artifacts"] != approved):
            raise ValueError("execution observation differs from approved engine and model identities")
        verified_installed_package_model("rapidocr", "docling_ocr")
    sources = value["project_sources_at_observation"]
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 256:
        raise ValueError("execution source observation exceeds bounds")
    expected["project_sources_at_observation"] = {_safe(name): _hex_digest(digest, label="project source digest")
                                                   for name, digest in sources.items()}
    runtime = _object(value["runtime"], set(expected["runtime"]))
    packages = runtime["packages"]
    expected_packages = expected["runtime"]["packages"]
    if not isinstance(packages, list) or len(packages) != len(expected_packages):
        raise ValueError("execution runtime package set differs")
    for observed, checked in zip(packages, expected_packages):
        _object(observed, set(checked))
        versions = observed["loaded_versions"]
        if not isinstance(versions, list) or len(versions) > 4:
            raise ValueError("loaded runtime versions exceed bounds")
        versions = [_safe(version, _VERSION) for version in versions]
        if versions != sorted(set(versions)):
            raise ValueError("loaded runtime versions are not canonical")
        pin = checked["required_version"]
        match = None if not versions or pin is None else all(
            version == pin or (checked["name"] == "opencv-python" and len(pin.split(".")) == 4
                               and version == pin.rsplit(".", 1)[0]) for version in versions)
        checked.update(loaded_versions=versions, loaded_version_agrees=match)
    matches = all(item["status"] == "version_match" and item["loaded_version_agrees"] is not False
                  for item in expected_packages)
    expected["runtime"]["package_versions_match"] = matches
    expected["requires_attention"] = (not expected["calls"] or expected["summary"]["failed"] != 0
                                      or installation_path is None or not matches)
    # JSON equality preserves bool/int distinctions unlike Python dictionary equality.
    if json.dumps(value, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
        raise ValueError("execution receipt contradicts its observations or current bindings")
    return expected
