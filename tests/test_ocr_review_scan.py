"""Separate original-scan UI over generated source-bound review artifacts.

The optional pixel replay port is supplied as generated in-memory gray pixels;
strict artifact parsing/replay is covered by the workspace/runtime suites.
No PDF, OCR, model or browser is used by these UI/CLI controls.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from ocr_review_runtime import ReviewWorkspace
from test_ocr_review_columns import _block, _capture_queued, _process
from test_ocr_review_columns import ui as ordinary_ui
from test_ocr_review_columns import workspace as workspace


def _page(number, status="available"):
    kinds = ("text_like", "rule_like", "ambiguous_ink")
    return {"page_number": number, "scan_status": status, "scan_reason": "bounded_pixel_discovery",
            "candidate_state": "available", "ocr_comparison_state": "supported",
            "layout_comparison_state": "supported", "regions": [
                {"region_id": f"scan-{index + 1:05d}", "kind": kind,
                 "bbox": [.1 + index * .2, .15, .2 + index * .2, .4], "ocr_status": "no_line_overlap",
                 "layout_status": "no_saved_region_overlap", "potential_ocr_omission": kind == "text_like",
                 "unmatched_in_both_saved_inputs": kind == "text_like"} for index, kind in enumerate(kinds)]}


@pytest.fixture
def scan_ui(workspace, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    pages = {number: _page(number) for number in (1, 2)}
    replay_calls, region_calls, failures = [], [], []
    source_image = Image.new("L", (120, 80), 123)

    def scan_page(number):
        workspace.verify_inputs()
        return copy.deepcopy(pages[number])

    def scan_render(number):
        workspace.verify_inputs()
        replay_calls.append(number)
        if failures:
            raise RuntimeError("SYNTHETIC_PRIVATE_PIXEL_MISMATCH")
        page = pages[number]
        if page is None or page["scan_status"] in {"not_requested", "unavailable"}:
            return None
        return source_image.copy()

    def scan_region(number, identifier):
        region_calls.append((number, identifier))
        image = scan_render(number)
        if image is None:
            raise ValueError("no physical original scan")
        return copy.deepcopy(next(item["bbox"] for item in pages[number]["regions"] if item["region_id"] == identifier))

    monkeypatch.setattr(workspace, "scan_page", scan_page, raising=False)
    monkeypatch.setattr(workspace, "scan_render", scan_render, raising=False)
    monkeypatch.setattr(workspace, "scan_region", scan_region, raising=False)
    generator = ordinary_ui.__wrapped__(workspace, monkeypatch)
    value = next(generator)
    value.pages, value.replay_calls, value.region_calls, value.failures = pages, replay_calls, region_calls, failures
    value.source_image = source_image
    yield value
    next(generator, None)


def _focus(ui, state=None, identifier="scan-00001"):
    state = ui.state if state is None else state
    return ui.functions["focus_scan_region"](state["page"], identifier, state, state["annotation_context"])[1]


def _export(ui):
    return next(block for block in ui.app.fns.values() if block.fn is not None and any(
        getattr(component, "label", None) == "I reviewed these crops against the scan" for component in block.inputs))


def test_separate_original_image_retains_every_hypothesis_kind_and_has_no_candidate_overlay(scan_ui):
    before_overlay_count = len(scan_ui.overlays)
    image, choices, notice, number = scan_ui.functions["scan_panels"](scan_ui.state)
    assert number == 1 and image.size == (120, 80)
    assert image.getpixel((0, 0)) == (123, 123, 123)
    assert len(choices["choices"]) == 3
    assert all(kind in str(choices) for kind in ("text_like", "rule_like", "ambiguous_ink"))
    assert "not proof" in notice and "Current saved candidate" in notice
    assert "Box overlap is not recognition" in notice
    assert len(scan_ui.overlays) == before_overlay_count
    image_component = next(block for block in scan_ui.app.blocks.values()
        if getattr(block, "label", None) == "Verified original source scan — independent pixel hypotheses")
    assert image_component.interactive is False and image_component.sources == []
    assert image_component.buttons == []


def test_focusing_only_draws_on_original_scan_copy_and_never_adds_a_crop(scan_ui):
    initial = copy.deepcopy(scan_ui.state)
    main_overlay_count = len(scan_ui.overlays)
    image, state, notice = scan_ui.functions["focus_scan_region"](1, "scan-00001", initial, initial["annotation_context"])
    assert image.mode == "RGB" and image.getpixel((12, 12)) == (216, 112, 0)
    assert scan_ui.source_image.getpixel((12, 12)) == 123
    assert state["scan_region_page"] == 1 and state["scan_region_id"] == "scan-00001"
    assert state["regions"] == initial["regions"] == [] and state["selections"] == initial["selections"]
    assert state["column_context"] == initial["column_context"]
    assert state["annotation_context"] != initial["annotation_context"]
    assert len(scan_ui.overlays) == main_overlay_count and "separate original scan" in notice


def test_add_uses_original_coordinates_directly_then_requires_new_crop_export_confirmation(scan_ui, monkeypatch):
    state = _focus(scan_ui)
    state["selections"]["crop"] = [.6, .6, .9, .9]

    def forbidden(*_args):
        pytest.fail("original coordinates were passed through candidate inverse preprocessing")

    monkeypatch.setattr(scan_ui.workspace.document, "source_rectangle", forbidden)
    prior = copy.deepcopy(state)
    state, preview, confirmed, notice = scan_ui.functions["add_scan_region"](1, "scan-00001", state, state["annotation_context"])
    assert confirmed is False and "no OCR ran" in notice
    assert state["regions"] == [{"region_id": "region-0001", "page_number": 1, "bbox": [.1, .15, .2, .4]}]
    assert state["selections"] == prior["selections"]
    assert json.loads(preview) == scan_ui.workspace.region_plan(state["regions"])
    with pytest.raises(Exception, match="annotation view changed"):
        _export(scan_ui).fn(True, state, prior["annotation_context"])
    with pytest.raises(Exception, match="review confirmation"):
        _export(scan_ui).fn(False, state, state["annotation_context"])
    path, _ = _export(scan_ui).fn(True, state, state["annotation_context"])
    assert json.loads(Path(path).read_bytes()) == scan_ui.workspace.region_plan(state["regions"])


@pytest.mark.parametrize("transition", ["page", "focus", "edit"])
@pytest.mark.parametrize("synchronize", [False, True])
def test_actual_queued_scan_add_cannot_follow_page_focus_or_annotation_edits(scan_ui, transition, synchronize):
    async def scenario():
        initial = _focus(scan_ui)
        token = initial["annotation_context"]
        session_hash = "generated-scan-captured-focus"
        session = scan_ui.app.state_holder[session_hash]
        state_id = _block(scan_ui, "preview").inputs[0]._id
        session[state_id] = initial
        preceding = {
            "page": (_block(scan_ui, "change_page"), [2, None, "", token]),
            "focus": (_block(scan_ui, "focus_scan_region"), [1, "scan-00002", None, token]),
            "edit": (_block(scan_ui, "annotation_input"), [None]),
        }[transition]
        events = [await _capture_queued(scan_ui, block, inputs, session_hash=session_hash)
                  for block, inputs in (preceding, (_block(scan_ui, "add_scan_region"), [1, "scan-00001", None, token]))]
        await _process(scan_ui, events[0].fn, events[0].data.data, session, session_hash)
        current = copy.deepcopy(session[state_id])
        if synchronize:
            synced = await _process(scan_ui, _block(scan_ui, "sync_annotation_context"), [None], session, session_hash)
            assert synced["data"] == [current["annotation_context"], False, False, False, False, ""]
        with pytest.raises(Exception, match="annotation view changed"):
            await _process(scan_ui, events[1].fn, events[1].data.data, session, session_hash)
        assert current == session[state_id] and current["regions"] == []
        if transition == "page":
            assert current["scan_region_id"] is None and current["scan_region_page"] is None
    asyncio.run(scenario())


@pytest.mark.parametrize("page,identifier", [(True, "scan-00001"), (1., "scan-00001"), (2, "scan-00001"),
                                           (1, "scan-00002"), (1, "unknown"), (1, None)])
def test_add_requires_exact_captured_page_and_region_even_with_current_token(scan_ui, page, identifier):
    state = _focus(scan_ui)
    before = copy.deepcopy(state)
    count = len(scan_ui.region_calls)
    with pytest.raises(Exception, match="Review action failed"):
        scan_ui.functions["add_scan_region"](page, identifier, state, state["annotation_context"])
    assert state == before and len(scan_ui.region_calls) == count


@pytest.mark.parametrize("token", [None, "", "stale", True, 1, [], {}])
def test_scan_focus_and_add_reject_missing_or_malformed_client_context_before_replay(scan_ui, token):
    state = _focus(scan_ui)
    count = len(scan_ui.region_calls)
    before = copy.deepcopy(state)
    for action in ("focus_scan_region", "add_scan_region"):
        with pytest.raises(Exception, match="annotation view changed"):
            scan_ui.functions[action](1, "scan-00001", state, token)
        assert state == before and len(scan_ui.region_calls) == count


@pytest.mark.parametrize("status", ["not_requested", "unavailable", "blank_at_threshold", "ambiguous"])
def test_no_render_unavailable_blank_and_ambiguous_have_distinct_visible_status(scan_ui, status):
    page = scan_ui.pages[1]
    page["scan_status"] = status
    page["scan_reason"] = {"not_requested": "outside_scan_cohort", "unavailable": "render_failed",
                           "blank_at_threshold": "no_dark_otsu_foreground", "ambiguous": "dense_foreground_not_text_classified"}[status]
    if status != "ambiguous":
        page["regions"] = []
    image, choices, notice, number = scan_ui.functions["scan_panels"](scan_ui.state)
    assert number == 1 and status in notice and "not proof" in notice
    assert (image is None) == (status in {"not_requested", "unavailable"})
    assert len(choices["choices"]) == (3 if status == "ambiguous" else 0)
    assert scan_ui.state["regions"] == []


@pytest.mark.parametrize("unavailable", ["ocr", "layout", "both"])
def test_unevaluated_correspondence_is_displayed_as_unknown_not_false(scan_ui, unavailable):
    if unavailable in {"ocr", "both"}:
        scan_ui.pages[1]["ocr_comparison_state"] = "unsupported_preprocessing"
    if unavailable in {"layout", "both"}:
        scan_ui.pages[1]["layout_comparison_state"] = "not_supplied"
    for region in scan_ui.pages[1]["regions"]:
        if unavailable in {"ocr", "both"}:
            region.update(ocr_status="unevaluated", potential_ocr_omission=False)
        if unavailable in {"layout", "both"}:
            region["layout_status"] = "unevaluated"
        region["unmatched_in_both_saved_inputs"] = False
    state = _focus(scan_ui)
    notice = scan_ui.functions["scan_panels"](state)[2]
    assert "unsupported_preprocessing" in notice or "not_supplied" in notice
    assert ("potential OCR omission true" if unavailable == "layout" else "potential OCR omission unknown") in notice
    assert "unmatched in both saved inputs unknown" in notice


def test_wrong_page_adapter_result_cannot_label_or_focus_a_scan(scan_ui):
    scan_ui.pages[1]["page_number"] = 2
    before = copy.deepcopy(scan_ui.state)
    assert scan_ui.functions["scan_panels"](before)[0] is None
    with pytest.raises(Exception, match="Review action failed"):
        scan_ui.functions["focus_scan_region"](1, "scan-00001", before, before["annotation_context"])
    assert before == scan_ui.state


def test_pixel_mismatch_remains_visible_and_cannot_add_or_change_main_editor(scan_ui):
    state = _focus(scan_ui)
    before = copy.deepcopy(state)
    scan_ui.failures.append(True)
    image, choices, notice, _ = scan_ui.functions["scan_panels"](state)
    assert image is None and choices["choices"] == []
    assert "could not be verified" in notice and "SYNTHETIC_PRIVATE" not in notice
    assert scan_ui.functions["refresh_panels"](state)[0] == scan_ui.workspace.document.page_text(1)
    for action in ("focus_scan_region", "add_scan_region"):
        with pytest.raises(Exception, match="Review action failed") as error:
            scan_ui.functions[action](1, "scan-00001", state, state["annotation_context"])
        assert "SYNTHETIC_PRIVATE" not in str(error.value) and state == before


def test_no_bundle_keeps_normal_review_available(ordinary_ui):
    image, choices, notice, number = ordinary_ui.functions["scan_panels"](ordinary_ui.state)
    assert image is None and choices["choices"] == [] and number == ordinary_ui.state["page"]
    assert "no fixed scan bundle" in notice
    state = ordinary_ui.state
    accepted = ordinary_ui.functions["add_reference"]("Generated checked text.", True, state, state["annotation_context"])[0]
    assert len(accepted["references"]) == 1


def test_draft_retains_original_crop_but_not_scan_focus_or_any_approval(scan_ui, monkeypatch):
    state = _focus(scan_ui)
    state = scan_ui.functions["add_scan_region"](1, "scan-00001", state, state["annotation_context"])[0]
    _, path, _ = scan_ui.functions["save_draft"]("", state, state["annotation_context"])
    payload = json.loads(Path(path).read_bytes())
    assert payload["regions"] == state["regions"]
    assert "scan_region_id" not in payload and "scan_region_page" not in payload
    loaded = ReviewWorkspace(scan_ui.workspace.pdf_path, scan_ui.workspace.recovery_path, scan_ui.workspace.output_dir,
                             proposals_path=scan_ui.workspace.proposals_path, draft_path=Path(path))
    monkeypatch.setattr(loaded, "render", scan_ui.workspace.render)
    from ocr_review_ui import build_app
    app = build_app(loaded)
    try:
        restored = next(block.value for block in app.blocks.values() if type(block).__name__ == "State")
        assert restored["regions"] == state["regions"] and restored["scan_region_id"] is None
        assert restored["scan_region_page"] is None
        assert all(block.value is False for block in app.blocks.values() if type(block).__name__ == "Checkbox")
    finally:
        app.close()


def test_real_scan_events_capture_page_region_and_context_then_sync_only_on_success(scan_ui):
    for name in ("focus_scan_region", "add_scan_region"):
        block = _block(scan_ui, name)
        assert [item.label for item in block.inputs if type(item).__name__ != "State"] == [
            "Original scan evidence page", "Original scan hypothesis to inspect", "Annotation view context"]
        assert block.inputs[0].stateful is False and block.inputs[1].stateful is False and block.inputs[-1].stateful is False
        key = next(key for key, candidate in scan_ui.app.fns.items() if candidate is block)
        child, = [item for item in scan_ui.app.config["dependencies"] if item["trigger_after"] == key]
        assert child["trigger_only_on_success"] is True
        assert scan_ui.app.fns[child["id"]].fn.__name__ == "sync_annotation_context"
    focus = _block(scan_ui, "focus_scan_region")
    dependency = next(item for item in scan_ui.app.config["dependencies"] if scan_ui.app.fns[item["id"]] is focus)
    assert [item[1] for item in dependency["targets"]] == ["input"]


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_scan_bundle_is_fixed_opt_in_and_directory_is_blocked_from_serving(tmp_path, monkeypatch, enabled):
    from tools import review_ocr
    import ocr_review_runtime

    bundle = tmp_path / "private-scan-bundle"
    recorded, launches = [], []

    def workspace(*args, **kwargs):
        recorded.append(kwargs)
        return SimpleNamespace(output_dir=tmp_path, pdf_path=tmp_path / "synthetic.pdf",
            recovery_path=tmp_path / "recovery.json", draft_path=None, proposals_path=None,
            scan_bundle_path=kwargs.get("scan_bundle_path"))

    app = SimpleNamespace(launch=lambda **kwargs: launches.append(kwargs), close=lambda: None)
    monkeypatch.setattr(ocr_review_runtime, "ReviewWorkspace", workspace)
    monkeypatch.setitem(sys.modules, "ocr_review_ui", SimpleNamespace(build_app=lambda _workspace: app))
    monkeypatch.setenv("RAG_OCR_REVIEW_TOKEN", "r_" + "x" * 32)
    # main refuses both; a developer shell must not fail this launch.
    monkeypatch.delenv("GRADIO_ALLOWED_PATHS", raising=False)
    monkeypatch.delenv("GRADIO_LOCAL_DEV_MODE", raising=False)
    args = ["--pdf", "synthetic.pdf", "--recovery", "recovery.json", "--output-dir", str(tmp_path), "--trusted-local-session"]
    if enabled:
        args += ["--scan-bundle", str(bundle)]
    assert review_ocr.main(args) == 0
    assert ("scan_bundle_path" in recorded[0]) is enabled
    assert (str(bundle) in launches[0]["blocked_paths"]) is enabled
    assert launches[0]["server_name"] == "127.0.0.1" and launches[0]["share"] is False
    assert launches[0]["allowed_paths"] == [] and launches[0]["mcp_server"] is False


def test_cli_scan_errors_never_echo_fixed_paths_or_native_details(tmp_path, monkeypatch, capsys):
    from tools import review_ocr
    import ocr_review_runtime

    def fail(*_args, **_kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_BUNDLE_ERROR")

    monkeypatch.setattr(ocr_review_runtime, "ReviewWorkspace", fail)
    monkeypatch.setenv("RAG_OCR_REVIEW_TOKEN", "r_" + "x" * 32)
    assert review_ocr.main(["--pdf", "synthetic.pdf", "--recovery", "recovery.json", "--output-dir", str(tmp_path),
        "--scan-bundle", "SYNTHETIC_PRIVATE_PATH", "--trusted-local-session"]) == 2
    captured = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE" not in captured.out + captured.err and "OCR review unavailable" in captured.err
