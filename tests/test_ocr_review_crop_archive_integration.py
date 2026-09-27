"""Real UI callbacks -> host -> private store -> historical scorer, generated only.

Both full historical archive validators run. Source byte verification and the
preview are explicitly inert; this is not browser, native decoding, OCR accuracy
or independent human transcription evidence. Restart here constructs fresh host
and session objects in one process, not a fresh interpreter.
"""

import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

import ocr_crop_review_pack as packs
from ocr_review import ReviewDocument
from ocr_review_crop_archive_ui import build_crop_archive_panel
from ocr_review_crop_packs import CropReviewPackService
from ocr_review_runtime import ReviewWorkspace
from test_ocr_disposition_archive import archive_fixture


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_full_archive_review_save_reopen_and_unreviewed_draft_revision(tmp_path, monkeypatch, operation):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    gr = pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    left = archive_fixture(operation, text="synthetic region")
    right = archive_fixture(operation, text="synthetic region!", dpi=400)
    root = tmp_path / "packs"
    root.mkdir()
    source = tmp_path / "declared-only-source.pdf"
    recovery = tmp_path / "declared-only-recovery.json"
    binding = {"source_sha256": left["source_sha256"], "page_count": left["page_count"],
               "recovery_sha256": hashlib.sha256(left["retained_inputs"]["recovery.json"]).hexdigest()}
    original = packs.build_crop_review_pack(**{
        side: {key: value[key] for key in ("artifacts", "retained_inputs")}
        for side, value in (("baseline", left), ("retry", right))}, **binding,
        baseline_region_id="r00", retry_region_id="r00", reference="synthetic region", critical_tokens_text="synthetic")
    services, apps, images, renders = [], [], [], []

    def make_host():
        workspace = object.__new__(ReviewWorkspace)
        workspace.document = ReviewDocument(json.loads(left["retained_inputs"]["recovery.json"]),
                                             recovery_sha256=binding["recovery_sha256"])
        workspace.pdf_path, workspace.recovery_path, workspace.output_dir = source, recovery, tmp_path
        workspace.verify_inputs = lambda: None  # Explicit source-verification boundary double.

        def render(scope, *, preview_profile, cancel_requested):
            assert preview_profile == "fit"
            assert cancel_requested() is False
            renders.append(copy.deepcopy(scope))
            image = Image.new("RGB", (60, 30), (20, 30, 40))
            images.append(image)
            return image

        service = CropReviewPackService(workspace, root, render_preview=render,
                                        preview_private_root=tmp_path / "inert-preview")
        services.append(service)
        app = gr.Blocks()
        with app:
            build_crop_archive_panel(workspace, service)
        apps.append(app)
        state = next(copy.deepcopy(item.value) for item in app.blocks.values()
                     if item.__class__.__name__ == "State")
        functions = {item.fn.__name__: item.fn for item in app.fns.values() if item.fn}
        return NS(service=service, app=app, state=state, functions=functions)

    def call(ui, name, *args):
        if name in {"open_crop_archive", "prepare_archive_reference", "score_archive_reference",
                    "save_archive_draft", "save_archive_reviewed"}:
            args = (*args, "fit")
        return ui.functions[name](ui.state, *args)

    def open_entry(ui, key):
        call(ui, "select_crop_archive", key, ui.state.generation)
        view = call(ui, "open_crop_archive", key, ui.state.generation)
        assert view[1] is not None, view[-1]
        assert view[9] is False and view[11] == ""
        assert ui.state.pending is None and ui.state.last_score is None
        return view

    try:
        first = make_host()
        saved = first.service._store.publish(original)
        call(first, "refresh_crop_archives")
        assert first.state.selected is None and set(first.state.catalog) == {saved["pack_id"]}
        view = open_entry(first, saved["pack_id"])
        assert view[6:8] == ("synthetic region", "synthetic")
        prepared = call(first, "prepare_archive_reference", saved["pack_id"], first.state.generation,
                        view[6], view[7], first.state.action_token)
        score = call(first, "score_archive_reference", saved["pack_id"], first.state.generation,
                     view[6], view[7], prepared[0], True, first.state.action_token)
        expected = json.loads(score[2])
        assert expected["status"] == "compared"
        call(first, "save_archive_reviewed", saved["pack_id"], first.state.generation,
             view[6], view[7], first.state.action_token)
        assert first.state.last_score is None
        catalog = first.service.catalog()
        assert len(catalog) == 2
        reviewed = next(row for row in catalog if row["pack_id"] != saved["pack_id"])
        child = first.service._store.read_for_revision(reviewed["pack_id"])
        assert child["evidence"]["review"]["reviewed"]["comparison"] == expected
        for name, raw in original["files"].items():
            if name != "review.json":
                assert child["pack"]["files"][name] == raw
        assert first.service.close() is True

        restarted = make_host()
        call(restarted, "refresh_crop_archives")
        assert restarted.state.selected is None
        assert set(restarted.state.catalog).isdisjoint({row["pack_id"] for row in catalog})
        reopened_id = next(key for key, row in restarted.state.catalog.items()
                           if row["manifest_sha256"] == reviewed["manifest_sha256"])
        reopened = open_entry(restarted, reopened_id)
        assert "Historical local declarations" in reopened[5] and restarted.state.last_score is None
        with pytest.raises(gr.Error):
            call(restarted, "save_archive_reviewed", reopened_id, restarted.state.generation,
                 reopened[6], reopened[7], restarted.state.action_token)
        call(restarted, "archive_reference_edited", restarted.state.generation, "  partial\n", "first\n",
             restarted.state.action_token)
        call(restarted, "save_archive_draft", reopened_id, restarted.state.generation,
             "  partial\n", "first\n", restarted.state.action_token)
        last = next(row for row in restarted.service.catalog() if row["pack_id"] not in restarted.state.catalog)
        evidence = restarted.service._store.read(last["pack_id"])
        assert evidence["review"]["reviewed"] is None
        assert evidence["review"]["draft"] == {"reference": "  partial\n", "critical_tokens_text": "first\n"}
        assert evidence["manifest"]["parent_pack_sha256"] == reviewed["manifest_sha256"]
        assert len(renders) == 2 and not source.exists() and not recovery.exists()
    finally:
        for service in services:
            service.close()
        for app in apps:
            app.close()
        for image in images:
            image.close()
