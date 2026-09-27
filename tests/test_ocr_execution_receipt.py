"""Execution evidence is local, bounded, content-free and never inferred from installs."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import ocr_execution_receipt as receipt


def observation():
    models = [{"id": role, "sha256": str(index) * 64, "bytes": 100}
              for index, role in enumerate(sorted(receipt._ROLES), 1)]
    return {"engine": {"name": "rapidocr", "version": "3.9.2"}, "model_artifacts": models,
            "sessions": [{"role": model["id"], "execution_providers": ["CPUExecutionProvider"],
                          "thread_settings": {"intra_op": 1, "inter_op": 1}, "model_sha256": model["sha256"]}
                         for model in models], "verification_scope": receipt._SCOPE}


@pytest.fixture
def capture(monkeypatch):
    def runtime(_path, **kwargs):
        return {"package_versions_match": True, "locked_environment_verified": False,
                "effective_runtime": kwargs.get("effective_runtime")}
    monkeypatch.setattr(receipt, "capture_runtime_manifest", runtime)
    monkeypatch.setattr(receipt, "_loaded_source_digests", lambda: {"ocr_execution_receipt": "e" * 64})
    def build(**kwargs):
        arguments = dict(operation="pages", inputs={"source_sha256": "a" * 64}, output_sha256="b" * 64,
                         observation=observation(), calls=[{"id": "call-0001", "status": "completed", "elapsed_seconds": .1}],
                         elapsed_seconds=.2)
        arguments.update(kwargs)
        return receipt.capture_execution_receipt(**arguments)
    return build


def test_capture_actual_sessions_not_metadata_attestation(capture):
    report = capture()
    assert report["ocr_executed"] and report["summary"] == {"attempted": 1, "completed": 1, "failed": 0}
    assert report["requires_attention"] and report["installation_evidence"] == "not_supplied"
    assert report["runtime"]["locked_environment_verified"] is False
    assert "not signed" in report["scope"] and "not loaded-byte" in report["scope"]
    assert str(Path.cwd()) not in json.dumps(report)


def test_session_construction_is_not_an_ocr_call(capture):
    report = capture(calls=[])
    assert not report["ocr_executed"] and report["requires_attention"]
    assert report["engine_observation"] is not None


def test_failed_actual_call_is_attempted_not_completed(capture):
    report = capture(calls=[{"id": "call-0001", "status": "failed", "elapsed_seconds": .1}], observation=None)
    assert report["ocr_executed"] and report["summary"]["completed"] == 0
    assert report["requires_attention"]


def test_completed_call_without_effective_sessions_fails(capture):
    with pytest.raises(ValueError):
        capture(observation=None)


def test_detachment_and_nonuniform_threads_preserve_per_role(capture):
    original = observation()
    original["sessions"][1]["thread_settings"]["intra_op"] = 2
    report = capture(observation=original)
    original["sessions"][0]["execution_providers"].append("FAKE")
    assert "FAKE" not in json.dumps(report)
    assert report["runtime"]["effective_runtime"]["thread_settings"] == {"intra_op": None, "inter_op": None}
    assert report["engine_observation"]["sessions"][1]["thread_settings"]["intra_op"] == 2


@pytest.mark.parametrize("changes", [
    {"operation": "PRIVATE /path"}, {"inputs": {}}, {"inputs": {"PRIVATE /path": "a" * 64}},
    {"inputs": {"source": "PRIVATE"}}, {"output_sha256": "PRIVATE"}, {"elapsed_seconds": True},
    {"elapsed_seconds": float("inf")}, {"elapsed_seconds": float("nan")}, {"elapsed_seconds": 10 ** 10000},
    {"elapsed_seconds": 0}, {"calls": [{"id": "x", "status": "PRIVATE", "elapsed_seconds": 0}]},
    {"calls": [{"id": "x", "status": "completed", "elapsed_seconds": True}]},
    {"calls": [{"id": "x", "status": "completed", "elapsed_seconds": 0}] * 2},
    {"calls": [{"id": str(i), "status": "completed", "elapsed_seconds": 0} for i in range(101)]},
])
def test_capture_rejects_malformed_or_unbounded_values_without_text(capture, changes):
    with pytest.raises(ValueError) as error:
        capture(**changes)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(extra="PRIVATE"),
    lambda v: v["engine"].update(name="PRIVATE"),
    lambda v: v["engine"].update(version="PRIVATE/path"),
    lambda v: v["model_artifacts"].pop(),
    lambda v: v["model_artifacts"][0].update(bytes=True),
    lambda v: v["model_artifacts"][0].update(bytes=0),
    lambda v: v["model_artifacts"][0].update(sha256="PRIVATE"),
    lambda v: v["sessions"][0].update(model_sha256="f" * 64),
    lambda v: v["sessions"][0].update(execution_providers=[]),
    lambda v: v["sessions"][0].update(execution_providers=["CPUExecutionProvider"] * 2),
    lambda v: v["sessions"][0]["thread_settings"].update(intra_op=True),
    lambda v: v["sessions"][0]["thread_settings"].update(inter_op=257),
    lambda v: v["sessions"].__setitem__(1, copy.deepcopy(v["sessions"][0])),
])
def test_strict_effective_observation(mutation):
    value = observation()
    mutation(value)
    with pytest.raises(ValueError) as error:
        receipt.validate_engine_observation(value)
    assert "PRIVATE" not in str(error.value)


def test_recorder_times_only_true_calls_and_closes_on_observation_failure():
    class Engine:
        def __call__(self, fail=False):
            if fail:
                raise KeyboardInterrupt()
            return "PRIVATE prediction"
    class Reader:
        def __init__(self):
            self._engine = None
            self.closed = False
        def _load_engine(self):
            if self._engine is None:
                self._engine = Engine()
            return self._engine
        def execution_observation(self):
            raise ValueError("observation failed")
        def close(self):
            self.closed = True
    recorder = receipt.ExecutionRecorder()
    reader = recorder.reader_factory(Reader)()
    reader._load_engine()
    assert recorder.calls == []
    assert reader._load_engine()() == "PRIVATE prediction"
    with pytest.raises(KeyboardInterrupt):
        reader._load_engine()(True)
    assert [c["status"] for c in recorder.calls] == ["completed", "failed"]
    assert "PRIVATE" not in json.dumps(recorder.calls)
    with pytest.raises(ValueError):
        reader.close()
    assert reader.closed
    recorder.calls = [{}] * 100
    with pytest.raises(ValueError):
        reader._load_engine()()
    assert len(recorder.calls) == 100


@pytest.fixture
def installation(tmp_path, monkeypatch):
    def write(path, data):
        path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(receipt, "ROOT", tmp_path)
    monkeypatch.setattr(receipt.sys, "prefix", str(tmp_path))
    executable = tmp_path / "interpreter"
    executable_digest = write(executable, b"synthetic executable")
    monkeypatch.setattr(receipt.sys, "executable", str(executable))
    monkeypatch.setattr(receipt.platform, "python_version", lambda: "3.12.10")
    monkeypatch.setattr(receipt.importlib.metadata, "distributions", lambda: [SimpleNamespace(metadata={"Name": "example"}, version="1.0")])
    locks = {name: write(tmp_path / name, b"lock") for name in
             ("requirements-full.lock", "requirements-test.lock", "requirements-lock-tools.lock")}
    steps = {}
    for name in receipt._INSTALL_STEPS:
        data = (json.dumps({"python_version": "3.12.10", "packages": [{"name": "example", "version": "1.0"}]}).encode()
                if name == "inventory" else b"private install output")
        steps[name] = {"status": "complete", "exit_code": 0, "elapsed_seconds": .1,
                       "log_sha256": write(tmp_path / (name + ".log"), data), "log_bytes": len(data)}
    payload = {"schema_version": 1, "kind": "ocr_locked_environment_installation", "recipe": "repository-full-cpu-hash-sync-v1",
               "installer_source_sha256": write(tmp_path / "installer-source.py", b"source"), "locks": locks,
               "steps": steps, "status": "complete", "installed_under_hash_locks": True, "scope": receipt._INSTALL_SCOPE,
               "environment_identity_sha256": receipt.environment_identity(tmp_path), "python_executable_sha256": executable_digest}
    path = tmp_path / "installation.json"
    def publish():
        write(path, json.dumps(payload).encode())
        return path
    publish()
    return path, payload, publish


def test_complete_installation_validates_all_local_bindings(installation):
    path, payload, _ = installation
    result, digest = receipt.validate_installation_evidence(path)
    assert result == payload and digest == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("field,value", [("schema_version", True), ("status", "failed"),
                                         ("installed_under_hash_locks", 1), ("scope", "attested"),
                                         ("environment_identity_sha256", "a" * 64),
                                         ("python_executable_sha256", "b" * 64)])
def test_installation_cannot_be_promoted_by_status_alone(installation, field, value):
    path, payload, publish = installation
    payload[field] = value
    publish()
    with pytest.raises(ValueError):
        receipt.validate_installation_evidence(path)


@pytest.mark.parametrize("filename", ["installer-source.py", "requirements-full.lock", "requirements-test.lock",
                                      "requirements-lock-tools.lock", "sync.log", "inventory.log", "interpreter"])
def test_mutated_installation_evidence_is_rejected(installation, filename):
    path, _, _ = installation
    (path.parent / filename).write_bytes(b"changed PRIVATE")
    with pytest.raises(ValueError) as error:
        receipt.validate_installation_evidence(path)
    assert "PRIVATE" not in str(error.value)


def test_added_changed_or_duplicate_distribution_is_drift(installation, monkeypatch):
    path, _, _ = installation
    for packages in ([SimpleNamespace(metadata={"Name": "example"}, version="2")],
                     [SimpleNamespace(metadata={"Name": "example"}, version="1.0")] * 2,
                     [SimpleNamespace(metadata={"Name": "different"}, version="1.0")]):
        monkeypatch.setattr(receipt.importlib.metadata, "distributions", lambda: packages)
        with pytest.raises(ValueError):
            receipt.validate_installation_evidence(path)


def test_observer_reads_real_session_api_and_rechecks_model_paths(monkeypatch, tmp_path):
    import model_artifacts
    model_paths = {role: tmp_path / role for role in receipt._ROLES}
    fixture = observation()
    artifact = SimpleNamespace(version="3.9.2", files=[SimpleNamespace(role=m["id"], content_sha256=m["sha256"], size=m["bytes"])
                                                     for m in fixture["model_artifacts"]])
    monkeypatch.setattr(model_artifacts, "package_model", lambda _: artifact)
    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", lambda *_: model_paths)
    session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"],
                              get_session_options=lambda: SimpleNamespace(intra_op_num_threads=1, inter_op_num_threads=1))
    engine = SimpleNamespace(**{attr: SimpleNamespace(session=SimpleNamespace(session=session)) for attr in receipt._ROLES.values()})
    assert receipt.observe_rapidocr_engine(engine, engine_version="3.9.2", model_paths=model_paths) == fixture
    with pytest.raises(ValueError):
        receipt.observe_rapidocr_engine(engine, engine_version="3.9.2", model_paths={**model_paths, "detection": tmp_path / "other"})
    with pytest.raises(ValueError):
        receipt.observe_rapidocr_engine(engine, engine_version="old", model_paths=model_paths)


def test_strict_receipt_readback_rechecks_approved_model_identities(monkeypatch):
    import model_artifacts
    fixture = observation()
    artifact = SimpleNamespace(version="3.9.2", files=[SimpleNamespace(role=m["id"], content_sha256=m["sha256"], size=m["bytes"])
                                                     for m in fixture["model_artifacts"]])
    monkeypatch.setattr(model_artifacts, "package_model", lambda _: artifact)
    calls = []
    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", lambda *args: calls.append(args))
    def build(observed):
        return receipt.capture_execution_receipt(operation="pages", inputs={"source_sha256": "a" * 64},
                                                 output_sha256="b" * 64, observation=observed,
                                                 calls=[{"id": "call-0001", "status": "completed", "elapsed_seconds": .1}],
                                                 elapsed_seconds=.2)
    original = build(fixture)
    assert receipt.validate_execution_receipt(original) == original
    assert calls == [("rapidocr", "docling_ocr")]
    forged = copy.deepcopy(fixture)
    forged["model_artifacts"][0]["sha256"] = "f" * 64
    forged["sessions"][0]["model_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        receipt.validate_execution_receipt(build(forged))
    forged = copy.deepcopy(fixture)
    forged["engine"]["version"] = "9.9.9"
    with pytest.raises(ValueError):
        receipt.validate_execution_receipt(build(forged))
