"""Receipt-bound publication tests for AI project upload packages."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import ai_project_export
import markdown_validation
import publication_core
import rag


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _chapter_text(number: int, *, page_marker: bool = True) -> str:
    marker = f"\n[PDF page {number}]\n" if page_marker else "\n"
    return (
        f"# Chapter {number}\n"
        f"{marker}\n"
        "<!-- CASE OPINION -->\n\n"
        f"Chapter {number} body.[^1]\n\n"
        "---\n\n"
        "## Endnotes\n\n"
        f"[^1]: Chapter {number} authority.\n"
    )


def _ready_run(
        monkeypatch, tmp_path: Path, *, chapter_count: int = 2,
        page_markers: bool = True, split_requested: bool = True):
    run_root = tmp_path / "Book"
    chapters_dir = run_root / "Chapters"
    chapters_dir.mkdir(parents=True)
    chunks = run_root / "Book_chunks.jsonl"
    chunks_raw = b'{"text":"bound source"}\n'
    chunks.write_bytes(chunks_raw)

    outputs = []
    for number in range(1, chapter_count + 1):
        name = f"ch{number:02d}_Chapter_{number}.md"
        raw = _chapter_text(
            number, page_marker=page_markers).encode("utf-8")
        (chapters_dir / name).write_bytes(raw)
        outputs.append({
            "role": name,
            "name": name,
            "size": len(raw),
            "sha256": _sha256(raw),
        })

    split_manifest = {
        "schema_version": rag.ARTIFACT_COMPLETION_SCHEMA_VERSION,
        "stage": "split_export",
        "source_sha256": _sha256(chunks_raw),
        "source_record_count": chapter_count,
        "parameters_sha256": "a" * 64,
        "outputs": outputs,
        "markdown_validation": {"status": "pass"},
    }
    split_path = rag._artifact_completion_path(
        chapters_dir, stage="split_export")
    split_path.write_text(
        json.dumps(split_manifest, sort_keys=True), encoding="utf-8")

    evidence = {
        name: {"test_generation": 1}
        for name in publication_core.PUBLICATION_GATE_NAMES
    }
    evidence["markdown_validity"].update({
        "split_requested": split_requested,
    })
    publication_receipt = publication_core.build_publication_receipt(evidence)
    publication_path = run_root / ".rag-publication.json"
    publication_path.write_bytes(
        publication_core.serialize_publication_receipt(publication_receipt))
    run_manifest = rag._retention.ensure_pipeline_run_manifest(
        tmp_path, run_root, job_scope="Book", owned_siblings=[],
        vector_stores=[])
    rag._retention.mark_pipeline_run_state(run_manifest, "complete")
    monkeypatch.setattr(
        rag, "_pipeline_publication_complete",
        lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        markdown_validation, "_probe_pandoc",
        lambda: markdown_validation._ToolProbe("unavailable"))
    monkeypatch.setattr(
        markdown_validation, "_probe_zettlr_validator",
        lambda: markdown_validation._ToolProbe("unavailable"))
    return SimpleNamespace(
        run_root=run_root,
        chunks=chunks,
        chapters_dir=chapters_dir,
        split_path=split_path,
        split_manifest=split_manifest,
        publication_path=publication_path,
        publication_receipt=publication_receipt,
    )


def test_claude_package_has_only_upload_files_and_external_receipt(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    output_dir = state.run_root / "Claude"

    result = rag.export_ai_project(
        state.chunks, output_dir, target="claude")

    assert result == output_dir
    assert {entry.name for entry in output_dir.iterdir()} == {
        "ch01_Chapter_1.txt", "ch02_Chapter_2.txt",
    }
    assert all(entry.is_file() and entry.suffix == ".txt"
               for entry in output_dir.iterdir())
    receipt_path = rag._ai_project_receipt_path(output_dir)
    assert receipt_path.is_file()
    assert receipt_path.parent == output_dir.parent
    assert receipt_path not in output_dir.iterdir()
    receipt = ai_project_export.parse_export_receipt_bytes(
        receipt_path.read_bytes())
    assert receipt["publication_receipt_sha256"] == _sha256(
        state.publication_path.read_bytes())
    assert receipt["publication_evidence_root_sha256"] == (
        state.publication_receipt["evidence_root_sha256"])
    assert receipt["canonical_split_manifest_sha256"] == _sha256(
        state.split_path.read_bytes())
    assert rag._ai_project_export_complete(
        output_dir, target="claude", chunks_path=state.chunks)


def test_grouped_package_relabels_endnotes_and_materializes_links_and_comments(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path, chapter_count=3)
    first = state.chapters_dir / "ch01_Chapter_1.md"
    first_text = first.read_text(encoding="utf-8").replace(
        "Chapter 1 body.",
        "[Continue with Chapter 2](ch02_Chapter_2.md)\n\nChapter 1 body.",
    )
    first.write_text(first_text, encoding="utf-8", newline="\n")
    first_record = state.split_manifest["outputs"][0]
    first_raw = first.read_bytes()
    first_record.update(size=len(first_raw), sha256=_sha256(first_raw))
    state.split_path.write_text(
        json.dumps(state.split_manifest, sort_keys=True), encoding="utf-8")
    output_dir = state.run_root / "ChatGPT"

    rag.export_ai_project(
        state.chunks, output_dir, target="chatgpt", max_files=1)

    assert {entry.name for entry in output_dir.iterdir()} == {
        "part01_ch01-ch03.md",
    }
    text = (output_dir / "part01_ch01-ch03.md").read_text(encoding="utf-8")
    assert "<!--" not in text
    assert "_Case opinion._" in text
    assert "(ch02_Chapter_2.md)" not in text
    assert "Continue with Chapter 2" in text
    for label in ("1", "2", "3"):
        assert text.count(f"[^{label}]") == 2
    receipt = ai_project_export.parse_export_receipt_bytes(
        rag._ai_project_receipt_path(output_dir).read_bytes())
    assert receipt["groups"] == [{
        "output": "part01_ch01-ch03.md",
        "sources": [
            "ch01_Chapter_1.md",
            "ch02_Chapter_2.md",
            "ch03_Chapter_3.md",
        ],
    }]


def test_export_requires_ready_split_publication(monkeypatch, tmp_path):
    state = _ready_run(
        monkeypatch, tmp_path, split_requested=False)
    output_dir = state.run_root / "NotebookLM"

    with pytest.raises(RuntimeError, match="--split-chapters"):
        rag.export_ai_project(
            state.chunks, output_dir, target="notebooklm")
    assert not output_dir.exists()

    monkeypatch.setattr(
        rag, "_pipeline_publication_complete",
        lambda *_args, **_kwargs: False)
    with pytest.raises(RuntimeError, match="currently READY"):
        rag.export_ai_project(
            state.chunks, output_dir, target="notebooklm")
    assert not output_dir.exists()


@pytest.mark.parametrize("mutation", ["chapter", "missing_page_marker"])
def test_export_rejects_stale_or_old_canonical_chapters(
        monkeypatch, tmp_path, mutation):
    state = _ready_run(
        monkeypatch, tmp_path, page_markers=mutation != "missing_page_marker")
    output_dir = state.run_root / "NotebookLM"
    if mutation == "chapter":
        with (state.chapters_dir / "ch01_Chapter_1.md").open(
                "ab") as stream:
            stream.write(b"tampered\n")

    expected = (
        "changed after publication"
        if mutation == "chapter" else "lacks visible PDF page markers"
    )
    with pytest.raises(ValueError, match=expected):
        rag.export_ai_project(
            state.chunks, output_dir, target="notebooklm")
    assert not output_dir.exists()


def test_unmanifested_target_entry_is_never_deleted_or_overwritten(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    output_dir = state.run_root / "ChatGPT"
    rag.export_ai_project(state.chunks, output_dir, target="chatgpt")
    foreign = output_dir / "keep-me.txt"
    foreign.write_text("user content", encoding="utf-8")

    assert not rag._ai_project_export_complete(
        output_dir, target="chatgpt", chunks_path=state.chunks)
    with pytest.raises(ValueError, match="unmanifested"):
        rag.export_ai_project(state.chunks, output_dir, target="chatgpt")
    assert foreign.read_text(encoding="utf-8") == "user content"


def test_modified_manifested_target_is_never_silently_overwritten(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    output_dir = state.run_root / "ChatGPT"
    rag.export_ai_project(state.chunks, output_dir, target="chatgpt")
    modified = output_dir / "ch01_Chapter_1.md"
    modified.write_text("USER MODIFIED CONTENT\n", encoding="utf-8")

    with pytest.raises(ValueError, match="modified or unmanifested"):
        rag.export_ai_project(state.chunks, output_dir, target="chatgpt")

    assert modified.read_text(encoding="utf-8") == "USER MODIFIED CONTENT\n"


def test_forged_output_hash_cannot_authorize_overwriting_user_edit(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    output_dir = state.run_root / "ChatGPT"
    rag.export_ai_project(state.chunks, output_dir, target="chatgpt")
    modified = output_dir / "ch01_Chapter_1.md"
    user_content = b"USER EDIT THAT MUST NOT BE OVERWRITTEN\n"
    modified.write_bytes(user_content)

    receipt_path = rag._ai_project_receipt_path(output_dir)
    forged = json.loads(receipt_path.read_text(encoding="utf-8"))
    record = next(
        item for item in forged["outputs"] if item["name"] == modified.name)
    record.update(
        size=len(user_content), sha256=_sha256(user_content))
    receipt_path.write_bytes(
        ai_project_export.serialize_export_receipt(forged))

    with pytest.raises(ValueError, match="ownership receipt is stale or modified"):
        rag.export_ai_project(state.chunks, output_dir, target="chatgpt")

    assert modified.read_bytes() == user_content


def test_forged_extra_record_cannot_authorize_deleting_user_file(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    output_dir = state.run_root / "ChatGPT"
    rag.export_ai_project(state.chunks, output_dir, target="chatgpt")
    user_file = output_dir / "ch03_User_Notes.md"
    user_content = b"IMPORTANT USER NOTES\n"
    user_file.write_bytes(user_content)

    receipt_path = rag._ai_project_receipt_path(output_dir)
    forged = json.loads(receipt_path.read_text(encoding="utf-8"))
    forged["groups"].append({
        "output": user_file.name,
        "sources": [user_file.name],
    })
    forged["outputs"].append({
        "name": user_file.name,
        "role": user_file.name,
        "size": len(user_content),
        "sha256": _sha256(user_content),
    })
    forged["source_page_markers"].append({
        "source": user_file.name,
        "count": 1,
    })
    validation = forged["content_validation"]
    validation["document_count"] += 1
    validation["source_document_count"] += 1
    validation["page_marker_count"] += 1
    validation["source_documents_with_page_markers"] += 1
    validation["output_documents_with_page_markers"] += 1
    receipt_path.write_bytes(
        ai_project_export.serialize_export_receipt(forged))

    with pytest.raises(ValueError, match="ownership receipt is stale or modified"):
        rag.export_ai_project(state.chunks, output_dir, target="chatgpt")

    assert user_file.read_bytes() == user_content


def test_partial_first_publication_is_retryable_from_exact_staged_bytes(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path, chapter_count=3)
    output_dir = state.run_root / "Claude"
    original = rag._promote_ai_project_staging

    def fail_after_one(stage_dir, target_dir, *, expected_records,
                       prior_records):
        rag._storage_policy.ensure_private_directory(target_dir)
        name = sorted(expected_records)[0]
        rag.os.replace(stage_dir / name, target_dir / name)
        raise RuntimeError("injected promotion failure")

    monkeypatch.setattr(
        rag, "_promote_ai_project_staging", fail_after_one)
    with pytest.raises(RuntimeError, match="injected promotion failure"):
        rag.export_ai_project(state.chunks, output_dir, target="claude")
    assert len(list(output_dir.iterdir())) == 1
    assert not rag._ai_project_receipt_path(output_dir).exists()
    assert not list(output_dir.parent.glob(
        f".{output_dir.name}.ai-project-stage-*"))

    monkeypatch.setattr(rag, "_promote_ai_project_staging", original)
    assert rag.export_ai_project(
        state.chunks, output_dir, target="claude") == output_dir
    assert rag._ai_project_export_complete(
        output_dir, target="claude", chunks_path=state.chunks)


def test_concurrent_target_writers_are_serialized_under_one_output_lease(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path, chapter_count=3)
    output_dir = state.run_root / "ChatGPT"
    start = threading.Barrier(2)

    def publish(max_files):
        start.wait(timeout=10)
        return rag.export_ai_project(
            state.chunks, output_dir, target="chatgpt",
            max_files=max_files)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, (1, 2)))

    assert results == [output_dir, output_dir]
    assert rag._ai_project_export_complete(
        output_dir, target="chatgpt", chunks_path=state.chunks)
    assert not list(output_dir.parent.glob(
        f".{output_dir.name}.ai-project-stage-*"))


def test_target_directory_cannot_contaminate_canonical_chapters(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    nested_target = state.chapters_dir / "Claude"

    with pytest.raises(ValueError, match="canonical target directory"):
        rag.export_ai_project(
            state.chunks, nested_target, target="claude")

    assert not nested_target.exists()


def test_target_directory_cannot_be_nested_in_another_ai_package(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    outer = state.run_root / "ChatGPT"
    rag.export_ai_project(state.chunks, outer, target="chatgpt")

    with pytest.raises(ValueError, match="canonical target directory"):
        rag.export_ai_project(
            state.chunks, outer / "Claude", target="claude")

    assert rag._ai_project_export_complete(
        outer, target="chatgpt", chunks_path=state.chunks)
    assert not (outer / "Claude").exists()


def test_target_directory_must_be_canonical_direct_child(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    custom = state.run_root / "My-Claude-Package"

    with pytest.raises(ValueError, match="canonical target directory"):
        rag.export_ai_project(state.chunks, custom, target="claude")

    assert not custom.exists()


def test_target_cannot_be_nested_in_foreign_run_tree(monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    foreign = tmp_path / "Other_Book" / "Chapters" / "Claude"

    with pytest.raises(ValueError, match="canonical target directory"):
        rag.export_ai_project(state.chunks, foreign, target="claude")

    assert not foreign.exists()


def test_target_cannot_nest_in_interrupted_custom_package(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    interrupted = state.run_root / "Interrupted_Package"
    interrupted.mkdir()
    partial = interrupted / "ch01_Chapter_1.txt"
    partial.write_text("interrupted output\n", encoding="utf-8")
    nested = interrupted / "Claude"

    with pytest.raises(ValueError, match="canonical target directory"):
        rag.export_ai_project(state.chunks, nested, target="claude")

    assert partial.read_text(encoding="utf-8") == "interrupted output\n"
    assert not nested.exists()


def test_canonical_target_symlink_is_rejected(monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    destination = tmp_path / "elsewhere"
    destination.mkdir()
    output_dir = state.run_root / "Claude"
    try:
        output_dir.symlink_to(destination, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises((OSError, ValueError)):
        rag.export_ai_project(state.chunks, output_dir, target="claude")

    assert list(destination.iterdir()) == []


def test_source_change_during_export_leaves_no_derivative_receipt(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    calls = 0

    def publication_is_ready(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return calls == 1

    monkeypatch.setattr(
        rag, "_pipeline_publication_complete", publication_is_ready)
    output_dir = state.run_root / "Claude"

    with pytest.raises(RuntimeError, match="changed during"):
        rag.export_ai_project(state.chunks, output_dir, target="claude")

    assert calls == 2
    assert not rag._ai_project_receipt_path(output_dir).exists()
    assert output_dir.is_dir()

    monkeypatch.setattr(
        rag, "_pipeline_publication_complete",
        lambda *_args, **_kwargs: True)
    assert rag.export_ai_project(
        state.chunks, output_dir, target="claude") == output_dir
    assert rag._ai_project_export_complete(
        output_dir, target="claude", chunks_path=state.chunks)


def test_completion_rechecks_live_ready_and_split_binding(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    output_dir = state.run_root / "NotebookLM"
    rag.export_ai_project(state.chunks, output_dir, target="notebooklm")
    assert rag._ai_project_export_complete(
        output_dir, target="notebooklm", chunks_path=state.chunks)

    state.split_path.write_bytes(state.split_path.read_bytes() + b" \n")

    assert not rag._ai_project_export_complete(
        output_dir, target="notebooklm", chunks_path=state.chunks)


def test_source_change_at_receipt_commit_cannot_publish_complete_package(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    output_dir = state.run_root / "Claude"
    original = rag._atomic_write_exact_utf8

    def mutate_source_before_receipt(path, content):
        if Path(path) == rag._ai_project_receipt_path(output_dir):
            payload = json.loads(state.split_path.read_text(encoding="utf-8"))
            payload["race_marker"] = "changed after final source check"
            state.split_path.write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8")
        return original(path, content)

    monkeypatch.setattr(
        rag, "_atomic_write_exact_utf8", mutate_source_before_receipt)
    with pytest.raises(RuntimeError, match="receipt did not verify"):
        rag.export_ai_project(state.chunks, output_dir, target="claude")

    assert not rag._ai_project_receipt_path(output_dir).exists()
    assert not rag._ai_project_export_complete(
        output_dir, target="claude", chunks_path=state.chunks)


def test_cli_routes_target_to_default_profile_directory(monkeypatch, tmp_path):
    chunks = tmp_path / "Book" / "Book_chunks.jsonl"
    calls = []
    monkeypatch.setattr(
        rag, "export_ai_project",
        lambda chunks_path, output_dir, *, target, max_files,
        validation_policy: calls.append((
            chunks_path, output_dir, target, max_files, validation_policy)))

    rag.main([
        "export", "--chunks", str(chunks), "--target", "claude",
        "--max-files", "2",
    ])

    assert calls == [(
        chunks, chunks.parent / "Claude", "claude", 2, "auto")]


def test_cli_routes_target_strict_validation(monkeypatch, tmp_path):
    chunks = tmp_path / "Book" / "Book_chunks.jsonl"
    calls = []
    monkeypatch.setattr(
        rag, "export_ai_project",
        lambda chunks_path, output_dir, *, target, max_files,
        validation_policy: calls.append((
            chunks_path, output_dir, target, max_files, validation_policy)))

    rag.main([
        "export", "--chunks", str(chunks), "--target", "chatgpt",
        "--markdown-validation", "strict",
    ])

    assert calls == [(
        chunks, chunks.parent / "ChatGPT", "chatgpt", None, "strict")]


def test_target_export_validates_materialized_documents_before_write(
        monkeypatch, tmp_path):
    state = _ready_run(monkeypatch, tmp_path)
    calls = []
    original = markdown_validation.MarkdownValidationSession.validate

    def observe(self, markdown, **kwargs):
        calls.append((self.policy, kwargs))
        return original(self, markdown, **kwargs)

    monkeypatch.setattr(
        markdown_validation.MarkdownValidationSession, "validate", observe)
    output_dir = state.run_root / "NotebookLM"

    rag.export_ai_project(
        state.chunks, output_dir, target="notebooklm",
        validation_policy="internal")

    assert len(calls) == 2
    assert all(policy == "internal" for policy, _ in calls)
    assert all(
        kwargs["require_table_markers"] is False
        for _, kwargs in calls)
