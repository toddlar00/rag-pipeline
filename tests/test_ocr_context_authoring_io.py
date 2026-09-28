"""Generated saved-source authoring exports; no render, model or OCR execution."""

import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
import stat

import pytest

import ocr_context_evaluation
import ocr_context_evaluation_io
import ocr_review_runtime as runtime
import storage_policy
from test_ocr_context_evaluation_io import inputs as inputs


OWNER = "generated-authoring-session"


@pytest.fixture
def environment(inputs):
    output = inputs["output"].parent / "review"
    output.mkdir()
    workspace = runtime.ReviewWorkspace(inputs["pdf"], inputs["recovery"], output)
    context = json.loads(inputs["reference"].read_bytes())["contexts"][0]
    mapping = json.loads(inputs["correspondence"].read_bytes())["contexts"][0]
    context["correspondence"] = {key: mapping[key] for key in ("status", "candidate_span")}
    authoring = {"contexts": [context], "selected_context_id": context["context_id"],
                 "selected_check_id": context["checks"][0]["check_id"]}
    return workspace, authoring


def export_pair(workspace, authoring):
    reference = workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    mapping = workspace.save_context_correspondence(authoring, reference, confirmed=True, owner=OWNER)
    return reference, mapping


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sequential_exports_bind_actual_bytes_and_preserve_sources(environment):
    workspace, authoring = environment
    original = workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes()
    draft = copy.deepcopy(authoring)
    reference, mapping = export_pair(workspace, authoring)
    result, report = workspace.evaluate_context_exports(reference, mapping, owner=OWNER)
    assert len({reference.path, mapping.path, report.path}) == 3
    assert result["coverage"]["checks"] == {"total": 1, "evaluated": 1, "passed": 1, "failed": 0, "abstained": 0}
    assert json.loads(mapping.path.read_bytes())["reference_sha256"] == digest(reference.path)
    assert result["inputs"] == {"pdf_sha256": digest(workspace.pdf_path),
                                "recovery_sha256": digest(workspace.recovery_path),
                                "reference_sha256": digest(reference.path),
                                "correspondence_sha256": digest(mapping.path)}
    assert json.loads(report.path.read_bytes()) == result
    assert authoring == draft
    assert original == (workspace.pdf_path.read_bytes(), workspace.recovery_path.read_bytes())
    for binding in (reference, mapping, report):
        assert binding.sha256 == digest(binding.path)
        assert binding.path.parent == workspace.output_dir
        if os.name == "nt":
            assert storage_policy.windows_path_is_private(binding.path, directory=False)
        else:
            assert stat.S_IMODE(binding.path.stat().st_mode) == 0o600


def test_focus_readback_is_detached_and_never_scores(environment, monkeypatch):
    workspace, authoring = environment
    reference, mapping = export_pair(workspace, authoring)
    result, binding = workspace.evaluate_context_exports(reference, mapping, owner=OWNER)
    result["contexts"].clear()
    monkeypatch.setattr(ocr_context_evaluation_io, "evaluate_context_files", lambda *_a, **_k: pytest.fail("scored during focus"))
    monkeypatch.setattr(ocr_context_evaluation, "evaluate_context_checks", lambda *_a, **_k: pytest.fail("scored during focus"))
    first = workspace.read_context_result(reference, mapping, binding, owner=OWNER)
    assert first["report"]["contexts"][0]["context_index"] == 1
    assert first["bindings"] == {"source_sha256": workspace.document.source_sha256,
                                 "recovery_sha256": workspace.document.recovery_sha256,
                                 "reference_sha256": reference.sha256,
                                 "correspondence_sha256": mapping.sha256}
    assert first["report_sha256"] == binding.sha256
    first["reference"]["contexts"][0]["source_anchor"]["bbox"][0] = .7
    first["correspondence"]["contexts"].clear()
    second = workspace.read_context_result(reference, mapping, binding, owner=OWNER)
    assert second["reference"]["contexts"][0]["source_anchor"]["bbox"][0] == .1
    assert len(second["correspondence"]["contexts"]) == 1
    assert len(list(workspace.output_dir.glob("ocr-context-*.json"))) == 3


def test_bindings_survive_copy_but_not_other_sessions_or_workspace(environment):
    workspace, authoring = environment
    reference = workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    assert copy.deepcopy(reference) is reference
    with pytest.raises(FrozenInstanceError):
        reference.sha256 = "f" * 64
    with pytest.raises(ValueError, match="session"):
        workspace.save_context_correspondence(authoring, reference, confirmed=True, owner="other-session")
    reopened = runtime.ReviewWorkspace(workspace.pdf_path, workspace.recovery_path, workspace.output_dir)
    with pytest.raises(ValueError, match="session"):
        reopened.save_context_correspondence(authoring, reference, confirmed=True, owner=OWNER)
    assert len(list(workspace.output_dir.glob("ocr-context-*.json"))) == 1


@pytest.mark.parametrize("forgery", ["dictionary", "path", "kind", "digest"])
def test_export_binding_refuses_browser_shaped_or_changed_identity(environment, forgery):
    workspace, authoring = environment
    reference = workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    bad = {"path": str(reference.path), "sha256": reference.sha256} if forgery == "dictionary" else replace(
        reference, **{"path": workspace.pdf_path} if forgery == "path" else
        {"kind": "references"} if forgery == "kind" else {"sha256": "f" * 64})
    with pytest.raises((ValueError, RuntimeError)):
        workspace.save_context_correspondence(authoring, bad, confirmed=True, owner=OWNER)
    assert len(list(workspace.output_dir.glob("ocr-context-*.json"))) == 1


@pytest.mark.parametrize("confirmation", [False, 1, None, "yes"])
def test_reference_and_mapping_require_explicit_current_confirmation(environment, confirmation):
    workspace, authoring = environment
    with pytest.raises(ValueError):
        workspace.save_context_reference(authoring, confirmed=confirmation, owner=OWNER)
    assert not list(workspace.output_dir.glob("ocr-context-*.json"))
    reference = workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    with pytest.raises(ValueError):
        workspace.save_context_correspondence(authoring, reference, confirmed=confirmation, owner=OWNER)
    assert len(list(workspace.output_dir.glob("ocr-context-*.json"))) == 1


def test_reference_text_change_requires_new_reference_export(environment):
    workspace, authoring = environment
    reference = workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    authoring["contexts"][0]["reference"] = authoring["contexts"][0]["reference"].replace("10", "20")
    with pytest.raises(ValueError, match="current draft"):
        workspace.save_context_correspondence(authoring, reference, confirmed=True, owner=OWNER)
    newer_reference, newer_mapping = export_pair(workspace, authoring)
    with pytest.raises(ValueError, match="generations"):
        workspace.evaluate_context_exports(reference, newer_mapping, owner=OWNER)
    result, _ = workspace.evaluate_context_exports(newer_reference, newer_mapping, owner=OWNER)
    assert result["coverage"]["checks"]["failed"] == 1
    assert json.loads(reference.path.read_bytes())["contexts"][0]["reference"].endswith("10 dollars.")


@pytest.mark.parametrize("changed", ["reference", "correspondence", "report", "source", "recovery"])
def test_result_focus_rechecks_every_exact_input_and_report(environment, changed):
    workspace, authoring = environment
    reference, mapping = export_pair(workspace, authoring)
    _, report = workspace.evaluate_context_exports(reference, mapping, owner=OWNER)
    target = {"reference": reference.path, "correspondence": mapping.path, "report": report.path,
              "source": workspace.pdf_path, "recovery": workspace.recovery_path}[changed]
    # Even a whitespace-only JSON change invalidates a raw-byte binding.
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="changed"):
        workspace.read_context_result(reference, mapping, report, owner=OWNER)
    assert len(list(workspace.output_dir.glob("ocr-context-*.json"))) == 3


@pytest.mark.parametrize("changed", ["source", "reference"])
def test_commit_rechecks_source_and_predecessor_after_serialization(environment, monkeypatch, changed):
    workspace, authoring = environment
    reference = workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    original = storage_policy.atomic_write_private_json

    def mutate(path, payload, **kwargs):
        publish = kwargs["replace_fn"]

        def changed_before_commit(temporary, destination):
            target = workspace.pdf_path if changed == "source" else reference.path
            target.write_bytes(target.read_bytes() + b"\n")
            return publish(temporary, destination)

        return original(path, payload, **{**kwargs, "replace_fn": changed_before_commit})

    monkeypatch.setattr(storage_policy, "atomic_write_private_json", mutate)
    with pytest.raises(RuntimeError, match="changed"):
        workspace.save_context_correspondence(authoring, reference, confirmed=True, owner=OWNER)
    assert list(workspace.output_dir.glob("ocr-context-correspondence-*.json")) == []
    assert reference.path.exists()


def test_postpublication_mismatch_preserves_file_without_returning_binding(environment, monkeypatch):
    workspace, authoring = environment
    original = runtime._publish_new_report

    def corrupt(temporary, destination):
        payload = json.loads(temporary.read_bytes())
        payload["contexts"][0]["reference"] = "different retained bytes"
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        return original(temporary, destination)

    monkeypatch.setattr(runtime, "_publish_new_report", corrupt)
    with pytest.raises(RuntimeError, match="reviewed payload"):
        workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    files = list(workspace.output_dir.glob("ocr-context-reference-*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_bytes())["contexts"][0]["reference"] == "different retained bytes"


def test_exports_are_nonclobber_even_when_host_name_repeats(environment, monkeypatch):
    workspace, authoring = environment

    class Fixed:
        hex = "1" * 32

    monkeypatch.setattr(runtime, "uuid4", Fixed)
    first = workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    raw = first.path.read_bytes()
    with pytest.raises(FileExistsError):
        workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    assert first.path.read_bytes() == raw
    assert len(list(workspace.output_dir.glob("ocr-context-*.json"))) == 1


@pytest.mark.parametrize("kind", ["reference", "correspondence"])
def test_serialized_limits_match_utf8_writer_including_final_newline(environment, monkeypatch, kind):
    import ocr_context_authoring as authoring_core

    workspace, authoring = environment
    reference = workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
    if kind == "reference":
        payload = authoring_core.build_reference(authoring, workspace.document, confirmed=True)
        constant = "MAX_CONTEXT_REFERENCE_BYTES"
    else:
        payload = authoring_core.build_correspondence(authoring, workspace.document,
            json.loads(reference.path.read_bytes()), reference.sha256, confirmed=True)
        constant = "MAX_CONTEXT_CORRESPONDENCE_BYTES"
    def publish():
        if kind == "reference":
            return workspace.save_context_reference(authoring, confirmed=True, owner=OWNER)
        return workspace.save_context_correspondence(authoring, reference, confirmed=True, owner=OWNER)

    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    monkeypatch.setattr(runtime, constant, len(raw) - 1)
    with pytest.raises(ValueError, match="byte limit"):
        publish()
    assert len(list(workspace.output_dir.glob("ocr-context-*.json"))) == 1
    monkeypatch.setattr(runtime, constant, len(raw))
    binding = publish()
    assert binding.path.read_bytes() == raw
