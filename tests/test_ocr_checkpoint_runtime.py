"""Checkpoint identity checks metadata and approved files without loading OCR."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

import model_artifacts
import ocr_checkpoint as core
import ocr_checkpoint_runtime as runtime


@pytest.fixture
def identity_runtime(tmp_path, monkeypatch):
    (tmp_path / "tools").mkdir()
    (tmp_path / "producer.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tools" / "worker.py").write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    observed = []
    def capture(path, **options):
        observed.append(("runtime", path, options))
        assert options == {"require_version_match": True}
    monkeypatch.setattr(runtime, "capture_runtime_manifest", capture)
    monkeypatch.setattr(runtime, "validate_installation_evidence", lambda path: observed.append(("installation", path)))
    monkeypatch.setattr(runtime.importlib.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "synthetic-package"}, version="1.2.3")])
    model = SimpleNamespace(files=[SimpleNamespace(role=role, content_sha256="f" * 64, size=100)
                                  for role in ("detection", "classification", "recognition")])
    monkeypatch.setattr(model_artifacts, "package_model", lambda name: model)
    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", lambda *args: observed.append(("models", args)))
    return tmp_path, observed, model


def test_identity_binds_producers_packages_interpreters_and_reverifies_models(identity_runtime):
    root, observed, _ = identity_runtime
    identity = runtime.capture_identity(installation_path=root / "installation.json")
    assert core.validate_identity(identity) == identity
    assert set(identity["producer_sources"]) == {"producer.py", "tools/worker.py"}
    assert ("installation", root / "installation.json") in observed
    assert ("models", ("rapidocr", "docling_ocr")) in observed
    assert str(root) not in core.json_bytes(identity).decode()
    runtime.capture_identity()
    assert sum(item[0] == "models" for item in observed) == 2


def test_source_edit_changes_identity_without_loading_model(identity_runtime):
    root, _, _ = identity_runtime
    before = runtime.capture_identity()
    (root / "producer.py").write_text("VALUE = 3\n", encoding="utf-8")
    after = runtime.capture_identity()
    assert before["producer_sources"] != after["producer_sources"]
    assert before["runtime_sha256"] == after["runtime_sha256"]


def test_shared_guard_modules_are_bound_without_a_fixed_producer_allowlist(identity_runtime):
    import hashlib
    import ocr_engine_guard
    import ocr_engine_limits

    root, _, _ = identity_runtime
    expected = {}
    for module in (ocr_engine_guard, ocr_engine_limits):
        raw = Path(module.__file__).read_bytes()
        name = Path(module.__file__).name
        (root / name).write_bytes(raw)
        expected[name] = hashlib.sha256(raw).hexdigest()
    before = runtime.capture_identity()
    assert all(before["producer_sources"][name] == value for name, value in expected.items())
    (root / "ocr_engine_limits.py").write_bytes(b"# synthetic changed allocation generation\n")
    after = runtime.capture_identity()
    assert before["producer_sources"]["ocr_engine_limits.py"] != after["producer_sources"]["ocr_engine_limits.py"]
    assert before["producer_sources"]["ocr_engine_guard.py"] == after["producer_sources"]["ocr_engine_guard.py"]


def test_installed_metadata_change_changes_runtime_identity(identity_runtime, monkeypatch):
    before = runtime.capture_identity()
    monkeypatch.setattr(runtime.importlib.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "synthetic-package"}, version="1.2.4")])
    assert runtime.capture_identity()["runtime_sha256"] != before["runtime_sha256"]


def test_duplicate_inventory_does_not_claim_stable_runtime(identity_runtime, monkeypatch):
    monkeypatch.setattr(runtime.importlib.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": name}, version="1") for name in ("some_name", "some-name")])
    with pytest.raises(ValueError):
        runtime.capture_identity()


@pytest.mark.parametrize("check", ["lock", "model", "installation"])
def test_failed_runtime_prerequisite_is_not_turned_into_identity(identity_runtime, monkeypatch, check):
    def fail(*_args, **_kwargs):
        raise ValueError("synthetic verification failed")
    if check == "lock":
        monkeypatch.setattr(runtime, "capture_runtime_manifest", fail)
    elif check == "model":
        monkeypatch.setattr(model_artifacts, "verified_installed_package_model", fail)
    else:
        monkeypatch.setattr(runtime, "validate_installation_evidence", fail)
    with pytest.raises(ValueError):
        runtime.capture_identity(installation_path=identity_runtime[0] / "installation.json")


def test_model_digest_change_is_bound(identity_runtime):
    _, _, model = identity_runtime
    before = copy.deepcopy(runtime.capture_identity())
    model.files[0].content_sha256 = "0" * 64
    assert runtime.capture_identity()["model_artifacts"] != before["model_artifacts"]


def test_second_verification_pass_growth_is_strictly_byte_bounded(tmp_path, monkeypatch):
    source = tmp_path / "growing.bin"
    source.write_bytes(b"12345678")
    original_open = Path.open
    observed = []
    class GrowingReader:
        def __init__(self, handle):
            self.handle, self.pass_number = handle, 0
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.handle.close()
        def fileno(self):
            return self.handle.fileno()
        def seek(self, position):
            self.pass_number += 1
            if self.pass_number == 2:
                with original_open(source, "ab") as writer:
                    writer.write(b"x" * 8192)
            return self.handle.seek(position)
        def read(self, count):
            result = self.handle.read(count)
            observed.append((self.pass_number, count, len(result)))
            return result
    def open_file(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return GrowingReader(handle) if path == source and args == ("rb",) else handle
    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(ValueError, match="streaming byte budget"):
        runtime.bounded_snapshot(source, 8)
    assert sum(size for attempt, _, size in observed if attempt == 2) <= 9
    assert all(0 < requested <= 9 for _, requested, _ in observed)
