"""Fixed synthetic OCR challenge contracts; no private inputs or model loads."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, fields
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import ocr_challenge as challenge
import ocr_recovery_comparison
import storage_policy


CASE_IDS = (
    "clean", "skew-positive", "skew-negative", "faint", "small-print",
    "columns", "table", "skew-faint",
)
DIGEST = "a" * 64
PDF_BYTES = b"%PDF-1.7\nsynthetic renderer stub only\n%%EOF\n"
LIBRARIES = {"pymupdf": "synthetic", "opencv": "synthetic", "numpy": "synthetic"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def stub_renderer(monkeypatch):
    calls = []

    def render():
        calls.append(True)
        return PDF_BYTES, copy.deepcopy(LIBRARIES)

    monkeypatch.setattr(challenge, "_render_pdf", render)
    return calls


def _directory_link(link, target):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _assert_private(path, *, directory=False):
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(path, directory=directory)
    else:
        expected = 0o700 if directory else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_import_remains_lazy_without_ocr_models_or_rendering_libraries():
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import ocr_challenge; "
            "forbidden={'rag','ocr_recovery_runtime','pymupdf','fitz','rapidocr',"
            "'onnxruntime','cv2','numpy','reportlab'}; "
            "assert not forbidden.intersection(sys.modules)"
        )], cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_fixed_cases_are_frozen_and_fit_one_explicit_retry_cohort():
    assert isinstance(challenge.CASES, tuple)
    assert tuple(case.case_id for case in challenge.CASES) == CASE_IDS
    assert len(challenge.CASES) == 8 <= 20
    for case in challenge.CASES:
        assert isinstance(case, challenge.ChallengeCase)
        assert isinstance(case.blocks, tuple)
        assert all(isinstance(block, tuple) for block in case.blocks)
        assert isinstance(case.critical_tokens, tuple)
        with pytest.raises(FrozenInstanceError):
            setattr(case, fields(case)[0].name, "changed")


def test_references_preserve_fixed_page_block_order_and_contextual_phrases():
    references = challenge._reference_payload(DIGEST)
    assert set(references) == {"schema_version", "source_sha256", "pages"}
    assert references["schema_version"] == 1
    assert references["source_sha256"] == DIGEST
    assert [page["page_number"] for page in references["pages"]] == list(range(1, 9))
    assert ocr_recovery_comparison.validate_references(references, page_count=8) is references
    for page, case in zip(references["pages"], challenge.CASES):
        assert " ".join(page["reference"].split()) == " ".join((
            challenge.TITLE, *(line for block in case.blocks for line in block), challenge.FOOTER))
        assert page["critical_tokens"] == list(case.critical_tokens)
        assert page["critical_tokens"]
        assert all(len(phrase.split()) >= 2 for phrase in page["critical_tokens"])
    columns = references["pages"][CASE_IDS.index("columns")]["reference"]
    blocks = challenge.CASES[CASE_IDS.index("columns")].blocks
    assert len(blocks) == 2
    assert columns.index(blocks[0][-1]) < columns.index(blocks[1][0])


def test_recipe_and_reference_payloads_are_deterministic_and_detached():
    references = challenge._reference_payload(DIGEST)
    recipe = challenge._recipe_payload()
    original_references, original_recipe = copy.deepcopy(references), copy.deepcopy(recipe)
    references["pages"][0]["reference"] = "changed"
    references["pages"][0]["critical_tokens"].append("changed")
    recipe.clear()
    assert challenge._reference_payload(DIGEST) == original_references
    assert challenge._recipe_payload() == original_recipe
    encoded = challenge._json_bytes(original_recipe)
    assert encoded.endswith(b"\n")
    assert json.loads(encoded) == json.loads(json.dumps(original_recipe))
    assert encoded == challenge._json_bytes(original_recipe)


def test_build_publishes_exact_hash_bound_private_artifacts(tmp_path, stub_renderer):
    output = tmp_path / "challenge"
    manifest = challenge.build_challenge(output)
    assert stub_renderer == [True]
    assert {path.name for path in output.iterdir()} == {"challenge.pdf", "references.json", "manifest.json"}
    assert (output / "challenge.pdf").read_bytes() == PDF_BYTES
    raw_references = (output / "references.json").read_bytes()
    references = json.loads(raw_references)
    assert references == challenge._reference_payload(hashlib.sha256(PDF_BYTES).hexdigest())
    assert manifest == json.loads((output / "manifest.json").read_bytes())
    assert set(manifest) == {
        "schema_version", "kind", "challenge_version", "source_sha256", "references_sha256",
        "recipe_sha256", "generator_sha256", "libraries", "page_count", "cases", "scope", "rendering",
    }
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "ocr_synthetic_challenge"
    assert manifest["challenge_version"] == "synthetic-ocr-v1"
    assert manifest["source_sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
    assert manifest["references_sha256"] == hashlib.sha256(raw_references).hexdigest()
    assert manifest["recipe_sha256"] == hashlib.sha256(challenge._json_bytes(challenge._recipe_payload())).hexdigest()
    assert manifest["generator_sha256"] == hashlib.sha256(Path(challenge.__file__).read_bytes()).hexdigest()
    assert manifest["libraries"] == LIBRARIES
    assert set(manifest["rendering"]) == {"page_size_points", "source_dpi", "font", "rotation", "tone"}
    assert manifest["rendering"]["page_size_points"] == list(challenge.PAGE_SIZE)
    assert manifest["rendering"]["source_dpi"] == challenge.SCAN_DPI
    assert manifest["page_count"] == len(manifest["cases"]) == 8
    assert [case["page_number"] for case in manifest["cases"]] == list(range(1, 9))
    assert [case["case_id"] for case in manifest["cases"]] == list(CASE_IDS)
    for entry, case in zip(manifest["cases"], challenge.CASES):
        assert set(entry) == {
            "page_number", "case_id", "layout", "font_size", "skew_degrees", "ink_level", "background_level",
        }
        for field in set(entry) - {"page_number"}:
            assert entry[field] == getattr(case, field)
    assert "synthetic" in manifest["scope"].lower()
    _assert_private(output, directory=True)
    for path in output.iterdir():
        _assert_private(path)


@pytest.mark.parametrize("existing", ("file", "empty_directory", "populated_directory"))
def test_existing_output_is_refused_before_rendering_without_changes(tmp_path, stub_renderer, existing):
    output = tmp_path / "challenge"
    if existing == "file":
        output.write_bytes(b"existing file")
    else:
        output.mkdir()
        if existing == "populated_directory":
            (output / "manifest.json").write_bytes(b"existing manifest")
    with pytest.raises((FileExistsError, storage_policy.StoragePolicyError)):
        challenge.build_challenge(output)
    assert stub_renderer == []
    if existing == "file":
        assert output.read_bytes() == b"existing file"
    elif existing == "populated_directory":
        assert (output / "manifest.json").read_bytes() == b"existing manifest"
    else:
        assert list(output.iterdir()) == []


def test_missing_parent_is_not_created_or_hardened(tmp_path, stub_renderer):
    parent = tmp_path / "missing-parent"
    with pytest.raises((ValueError, OSError)):
        challenge.build_challenge(parent / "challenge")
    assert not parent.exists()
    assert stub_renderer == []


@pytest.mark.parametrize("leaf", (False, True), ids=("parent_alias", "output_alias"))
def test_linked_output_paths_are_rejected_before_rendering(tmp_path, stub_renderer, leaf):
    target = tmp_path / "target"
    target.mkdir()
    (target / "sentinel.txt").write_bytes(b"untouched")
    link = tmp_path / "alias"
    _directory_link(link, target)
    output = link if leaf else link / "challenge"
    with pytest.raises(storage_policy.StoragePolicyError):
        challenge.build_challenge(output)
    assert stub_renderer == []
    assert {path.name for path in target.iterdir()} == {"sentinel.txt"}
    assert (target / "sentinel.txt").read_bytes() == b"untouched"


def test_manifest_is_the_last_completion_marker(tmp_path, stub_renderer, monkeypatch):
    output = tmp_path / "challenge"
    actual_link = os.link
    committed = []

    def observe(source, destination, *args, **kwargs):
        destination = Path(destination)
        if destination.parent == output:
            if destination.name == "manifest.json":
                assert (output / "challenge.pdf").read_bytes() == PDF_BYTES
                assert json.loads((output / "references.json").read_bytes())["source_sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
            committed.append(destination.name)
        return actual_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", observe)
    challenge.build_challenge(output)
    assert committed == ["challenge.pdf", "references.json", "manifest.json"]


@pytest.mark.parametrize("stage", ("challenge.pdf", "references.json", "manifest.json"))
def test_publication_failure_leaves_partial_directory_without_completion_or_retry(tmp_path, stub_renderer, monkeypatch, stage):
    output = tmp_path / "challenge"
    actual_link = os.link

    def fail(source, destination, *args, **kwargs):
        if Path(destination).name == stage:
            raise OSError("synthetic publication failure")
        return actual_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OSError):
        challenge.build_challenge(output)
    assert output.is_dir()
    assert not (output / "manifest.json").exists()
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(FileExistsError):
        challenge.build_challenge(output)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert stub_renderer == [True]


def test_references_and_recipe_are_frozen_before_rendering(tmp_path, stub_renderer, monkeypatch):
    actual_references = challenge._reference_payload
    actual_recipe = challenge._recipe_payload
    actual_render = challenge._render_pdf
    events = []

    def references(digest):
        events.append("references")
        return actual_references(digest)

    def recipe():
        events.append("recipe")
        return actual_recipe()

    def render():
        events.append("render")
        assert events.index("references") < events.index("render")
        assert events.index("recipe") < events.index("render")
        return actual_render()

    monkeypatch.setattr(challenge, "_reference_payload", references)
    monkeypatch.setattr(challenge, "_recipe_payload", recipe)
    monkeypatch.setattr(challenge, "_render_pdf", render)
    challenge.build_challenge(tmp_path / "challenge")
    assert events.count("references") == events.count("recipe") == events.count("render") == 1


def test_generator_change_during_rendering_prevents_any_artifact_publication(tmp_path, stub_renderer, monkeypatch):
    actual_read_bytes = Path.read_bytes
    source = Path(challenge.__file__)
    calls = []

    def read_bytes(path):
        if path == source:
            calls.append(True)
            return b"synthetic code generation one" if len(calls) == 1 else b"synthetic code generation two"
        return actual_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    output = tmp_path / "challenge"
    with pytest.raises(RuntimeError):
        challenge.build_challenge(output)
    assert len(calls) == 2
    assert stub_renderer == [True]
    assert output.is_dir() and list(output.iterdir()) == []


def test_output_directory_identity_is_pinned_across_rendering(tmp_path, monkeypatch):
    output = tmp_path / "challenge"
    moved = tmp_path / "original-directory"

    def render():
        output.rename(moved)
        output.mkdir()
        (output / "sentinel.txt").write_bytes(b"replacement directory")
        return PDF_BYTES, copy.deepcopy(LIBRARIES)

    monkeypatch.setattr(challenge, "_render_pdf", render)
    with pytest.raises(RuntimeError):
        challenge.build_challenge(output)
    assert list(moved.iterdir()) == []
    assert {path.name for path in output.iterdir()} == {"sentinel.txt"}


def test_existing_parent_permissions_are_not_changed(tmp_path, stub_renderer):
    parent = tmp_path / "existing-parent"
    parent.mkdir()
    if os.name != "nt":
        parent.chmod(0o755)
    original = stat.S_IMODE(parent.stat().st_mode)
    challenge.build_challenge(parent / "challenge")
    assert stat.S_IMODE(parent.stat().st_mode) == original


def test_stubbed_build_never_imports_ocr_or_image_dependencies(tmp_path):
    code = "\n".join((
        "import builtins, sys",
        "from pathlib import Path",
        "import ocr_challenge",
        "original_import = builtins.__import__",
        "forbidden = {'rag','ocr_recovery_runtime','pymupdf','fitz','rapidocr','onnxruntime','cv2','numpy','reportlab'}",
        "def guarded(name, *args, **kwargs):",
        "    if name.split('.')[0] in forbidden: raise AssertionError('unexpected heavy dependency')",
        "    return original_import(name, *args, **kwargs)",
        "builtins.__import__ = guarded",
        "ocr_challenge._render_pdf = lambda: (b'%PDF-synthetic', {'pymupdf':'synthetic','opencv':'synthetic','numpy':'synthetic'})",
        "ocr_challenge.build_challenge(Path(sys.argv[1]))",
    ))
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "challenge")], cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_real_renderer_is_reproducible_and_image_only_when_dependencies_available():
    fitz = pytest.importorskip("pymupdf")
    pytest.importorskip("cv2")
    pytest.importorskip("numpy")
    first, libraries = challenge._render_pdf()
    second, second_libraries = challenge._render_pdf()
    assert isinstance(first, bytes) and first.startswith(b"%PDF-")
    assert first == second
    assert len(first) <= challenge.MAX_PDF_BYTES
    assert set(libraries) == {"pymupdf", "opencv", "numpy"}
    assert libraries == second_libraries
    with fitz.open(stream=first, filetype="pdf") as document:
        assert len(document) == len(challenge.CASES) == 8
        for page in document:
            assert not page.get_text().strip()
            assert len(page.get_images()) == 1
            assert tuple(page.rect)[2:] == challenge.PAGE_SIZE


@pytest.mark.parametrize("error", (ImportError("synthetic unavailable dependency"), RuntimeError("synthetic render failure"), KeyboardInterrupt()))
def test_render_failure_or_cancellation_never_fabricates_completion(tmp_path, monkeypatch, error):
    output = tmp_path / "challenge"

    def fail():
        raise error

    monkeypatch.setattr(challenge, "_render_pdf", fail)
    with pytest.raises(type(error)):
        challenge.build_challenge(output)
    assert output.is_dir()
    assert not (output / "manifest.json").exists()
    with pytest.raises(FileExistsError):
        challenge.build_challenge(output)


@pytest.mark.parametrize("artifact", ("challenge.pdf", "references.json", "manifest.json"))
def test_uncooperative_existing_artifact_is_never_overwritten(tmp_path, monkeypatch, artifact):
    output = tmp_path / "challenge"

    def render():
        (output / artifact).write_bytes(b"uncooperative writer artifact")
        return PDF_BYTES, copy.deepcopy(LIBRARIES)

    monkeypatch.setattr(challenge, "_render_pdf", render)
    with pytest.raises(FileExistsError):
        challenge.build_challenge(output)
    assert (output / artifact).read_bytes() == b"uncooperative writer artifact"


@pytest.mark.parametrize("invalid", (b"", b"not a PDF", "%PDF-text-not-bytes", b"%PDF-" + b"x" * (16 * 1024 * 1024)),
                         ids=("empty", "wrong_magic", "text_not_bytes", "oversized"))
def test_invalid_or_oversized_renderer_bytes_cannot_get_completion_marker(tmp_path, monkeypatch, invalid):
    output = tmp_path / "challenge"
    monkeypatch.setattr(challenge, "_render_pdf", lambda: (invalid, copy.deepcopy(LIBRARIES)))
    with pytest.raises(ValueError):
        challenge.build_challenge(output)
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize("artifact", ("challenge.pdf", "references.json"))
def test_changed_published_artifact_prevents_completion_manifest(tmp_path, stub_renderer, monkeypatch, artifact):
    output = tmp_path / "challenge"
    actual_write = storage_policy.atomic_write_private

    def tamper(path, *args, **kwargs):
        result = actual_write(path, *args, **kwargs)
        if Path(path).name == "references.json":
            (output / artifact).write_bytes(b"changed after publication")
        return result

    monkeypatch.setattr(storage_policy, "atomic_write_private", tamper)
    with pytest.raises(RuntimeError):
        challenge.build_challenge(output)
    assert not (output / "manifest.json").exists()
    assert (output / artifact).read_bytes() == b"changed after publication"


@pytest.mark.parametrize("artifact", ("challenge.pdf", "references.json"))
def test_link_replacing_published_artifact_prevents_completion_manifest(tmp_path, stub_renderer, monkeypatch, artifact):
    output = tmp_path / "challenge"
    other = tmp_path / "other-artifact.bin"
    other.write_bytes(b"untouched target")
    # Verify platform link privileges before creating any challenge artifacts.
    probe = tmp_path / "link-probe"
    try:
        probe.symlink_to(other)
    except OSError:
        pytest.skip("file symlink creation is unavailable")
    probe.unlink()
    actual_write = storage_policy.atomic_write_private

    def substitute(path, *args, **kwargs):
        result = actual_write(path, *args, **kwargs)
        if Path(path).name == "references.json":
            target = output / artifact
            target.unlink()
            target.symlink_to(other)
        return result

    monkeypatch.setattr(storage_policy, "atomic_write_private", substitute)
    with pytest.raises(storage_policy.StoragePolicyError):
        challenge.build_challenge(output)
    assert not (output / "manifest.json").exists()
    assert other.read_bytes() == b"untouched target"


@pytest.mark.parametrize("error", (ValueError("synthetic oversized artifact"), OSError("synthetic unreadable artifact"), RuntimeError("synthetic changing artifact")))
def test_snapshot_failure_after_publication_cannot_get_completion_marker(tmp_path, stub_renderer, monkeypatch, error):
    output = tmp_path / "challenge"
    calls = []

    def fail(path, *, label, max_bytes):
        calls.append((Path(path).name, max_bytes))
        raise error

    monkeypatch.setattr(challenge, "_read_snapshot", fail)
    with pytest.raises(type(error)):
        challenge.build_challenge(output)
    assert calls == [("challenge.pdf", challenge.MAX_PDF_BYTES)]
    assert {path.name for path in output.iterdir()} == {"challenge.pdf", "references.json"}


def test_generator_change_after_artifact_writes_prevents_completion(tmp_path, stub_renderer, monkeypatch):
    actual_read = Path.read_bytes
    source = Path(challenge.__file__)
    calls = []

    def read(path):
        if path == source:
            calls.append(True)
            return b"unchanged source generation" if len(calls) <= 2 else b"changed source generation"
        return actual_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    output = tmp_path / "challenge"
    with pytest.raises(RuntimeError):
        challenge.build_challenge(output)
    assert len(calls) == 3
    assert {path.name for path in output.iterdir()} == {"challenge.pdf", "references.json"}


def test_commit_closure_refuses_directory_substitution_after_manifest_staging(tmp_path, stub_renderer, monkeypatch):
    output = tmp_path / "challenge"
    moved = tmp_path / "original-directory"
    actual_write = storage_policy.atomic_write_private

    def write(path, producer, **kwargs):
        publish = kwargs["replace_fn"]

        def substitute(temporary, destination):
            output.rename(moved)
            output.mkdir()
            (output / "sentinel.txt").write_bytes(b"replacement directory")
            return publish(temporary, destination)

        if Path(path).name == "manifest.json":
            kwargs["replace_fn"] = substitute
        return actual_write(path, producer, **kwargs)

    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises(RuntimeError):
        challenge.build_challenge(output)
    assert not (output / "manifest.json").exists()
    assert not (moved / "manifest.json").exists()
    assert {path.name for path in output.iterdir()} == {"sentinel.txt"}
    assert (moved / "challenge.pdf").read_bytes() == PDF_BYTES
    assert (moved / "references.json").is_file()
