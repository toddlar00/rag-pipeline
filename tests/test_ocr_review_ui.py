"""Real optional Gradio component/event wiring over synthetic in-memory input."""

import copy
import os
from types import SimpleNamespace

import pytest

from ocr_review import ReviewDocument
from test_ocr_review import missing_report, report


@pytest.fixture(scope="module")
def ui_app(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("ocr-review-ui")
    # The production factory sets these before its lazy import too.
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    from ocr_review_ui import build_app

    value = report()
    value["page_count"] = 2
    value["selection"]["requested_pages"] = [1, 2]
    second = copy.deepcopy(value["pages"][0])
    second["page_number"] = 2
    value["pages"].append(second)
    value["summary"].update(inspected=2, selected=2, review_required=2)
    document = ReviewDocument(value, recovery_sha256="b" * 64)
    exports = []
    failure = []

    def region_plan(regions):
        from ocr_regions import validate_region_plan
        return validate_region_plan({"schema_version": 1, "source_sha256": document.source_sha256,
                                     "recovery_sha256": document.recovery_sha256,
                                     "coordinate_system": "original_page_display_fraction", "regions": regions},
                                    source_sha256=document.source_sha256, recovery_sha256=document.recovery_sha256,
                                    page_count=document.page_count)

    def save(kind, payload):
        if failure:
            raise failure[0]
        exports.append((kind, copy.deepcopy(payload)))
        return tmp_path / "synthetic-export.json"

    workspace = SimpleNamespace(document=document, render=lambda _: Image.new("RGB", (100, 100), "white"),
                                region_plan=region_plan, save=save,
                                # In-memory callbacks use fake exports; real transport is covered separately.
                                prepare_download=lambda path, _cache: path)
    app = build_app(workspace)
    functions = {block.fn.__name__: block.fn for block in app.fns.values() if block.fn is not None}
    states = [block for block in app.blocks.values() if block.__class__.__name__ == "State"]
    app._test_export_failure = failure
    yield app, functions, copy.deepcopy(states[0].value), exports
    app.close()


@pytest.fixture
def ui(ui_app):
    app, functions, state, exports = ui_app
    return app, functions, copy.deepcopy(state), exports


def test_ui_does_not_publish_machine_events(ui):
    app, _, _, _ = ui
    assert app.analytics_enabled is False
    assert all(dep["api_visibility"] == "private" for dep in app.config["dependencies"])


def test_explicit_execution_panel_receives_existing_review_context(monkeypatch):
    import sys

    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    from ocr_review_ui import build_app

    captured = []
    coordinator = object()
    document = ReviewDocument(report(), recovery_sha256="b" * 64)
    workspace = SimpleNamespace(document=document, render=lambda _: Image.new("RGB", (100, 100), "white"))

    def bind(bound_workspace, bound_coordinator, **components):
        captured.append((bound_workspace, bound_coordinator, components))

    monkeypatch.setitem(sys.modules, "ocr_review_execution_ui", SimpleNamespace(build_execution_panel=bind))
    app = build_app(workspace, execution_coordinator=coordinator)
    try:
        assert len(captured) == 1
        bound_workspace, bound_coordinator, components = captured[0]
        assert bound_workspace is workspace and bound_coordinator is coordinator
        assert set(components) == {"review_state", "annotation_view"}
        assert components["review_state"].value["annotation_context"] == components["annotation_view"].value
        assert components["annotation_view"].__class__.__name__ != "State"
        assert workspace.document is document
    finally:
        app.close()


def test_default_real_app_does_not_import_execution_panel(monkeypatch):
    import sys

    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    from ocr_review_ui import build_app

    monkeypatch.setitem(sys.modules, "ocr_review_execution_ui", None)
    workspace = SimpleNamespace(document=ReviewDocument(report(), recovery_sha256="b" * 64),
                                render=lambda _: Image.new("RGB", (100, 100), "white"))
    app = build_app(workspace)
    app.close()


def test_two_click_crop_can_export_valid_plan(ui):
    _, functions, state, _ = ui
    select = functions["select_rectangle"]
    _, state, message, confirmed = select(state, "crop", SimpleNamespace(index=[10, 20]), state["annotation_context"])
    assert confirmed is False
    assert "opposite" in message
    _, state, _, _ = select(state, "crop", SimpleNamespace(index=[90, 80]), state["annotation_context"])
    assert state["first"] is None
    state, plan, confirmed, _ = functions["add_region"](state, state["annotation_context"])
    assert '"original_page_display_fraction"' in plan
    assert state["regions"][0]["bbox"] == [.1, .2, .9, .8]
    assert confirmed is False


def test_preview_abstention_removes_stale_page_plan(ui):
    _, functions, state, _ = ui
    state["selections"] = {"body": [0, .1, 1, .8], "gutter": [.45, .1, .55, .8]}
    _, state, proposed, difference, confirmed, _ = functions["preview"](state, False, state["column_context"])
    assert state["layouts"]
    assert "Alice\nYes\nBob" in proposed
    assert "--- original" in difference
    assert confirmed is False
    state["selections"]["gutter"] = [.3, .1, .7, .8]
    _, state, _, _, _, message = functions["preview"](state, False, state["column_context"])
    assert "abstained" in message
    assert state["layouts"] == []


def test_reference_cannot_be_stored_without_review(ui):
    _, functions, state, _ = ui
    with pytest.raises(Exception, match="Review action failed"):
        functions["add_reference"]("new reference", False, state, state["annotation_context"])
    assert state["references"] == []
    state, confirmed, _, export_confirmed = functions["add_reference"]("new reference", True, state, state["annotation_context"])
    assert export_confirmed is False
    assert state["references"][0]["reference"] == "new reference"
    assert confirmed is False


def test_switching_page_resets_active_selection_and_consent(ui):
    _, functions, state, _ = ui
    state.update(first=[10, 10], selections={"crop": [.1, .1, .9, .9]})
    result = functions["change_page"](1, state, view_context=state["annotation_context"])
    assert result[5:7] == (False, False)
    assert result[7]["first"] is None
    assert result[7]["selections"] == {}


def test_bad_event_errors_are_static(ui):
    _, functions, state, _ = ui
    with pytest.raises(Exception) as error:
        functions["select_rectangle"](state, "SYNTHETIC_SECRET", SimpleNamespace(index=[0, 0]), state["annotation_context"])
    assert "SYNTHETIC_SECRET" not in str(error.value)


def test_reference_content_is_not_rendered_as_markup(ui):
    _, functions, state, _ = ui
    content = "<script>SYNTHETIC_PRIVATE</script>"
    state, _, _, _ = functions["add_reference"](content, True, state, state["annotation_context"])
    assert state["references"][0]["reference"] == content


def test_selection_change_invalidates_layout_and_consent(ui):
    _, functions, state, _ = ui
    state["layouts"] = [{"page_number": 1}]
    state["line_order"] = [0, 1, 3, 2, 4, 5]
    _, state, _, confirmed = functions["select_rectangle"](state, "body", SimpleNamespace(index=[10, 20]), state["annotation_context"])
    assert state["layouts"] == []
    assert state["line_order"] is None
    assert confirmed is False


def test_undo_crop_and_clear_are_explicit(ui):
    _, functions, state, _ = ui
    state["regions"] = [{"region_id": "one"}, {"region_id": "two"}]
    state, _, confirmed, _ = functions["undo_region"](state, state["annotation_context"])
    assert state["regions"] == [{"region_id": "one"}]
    assert confirmed is False
    state["layouts"] = [{"page_number": 1}]
    result = functions["clear_selection"](state, state["annotation_context"])
    assert result[1]["layouts"] == []
    assert result[1]["regions"] == [{"region_id": "one"}]
    assert result[-1] is False


def test_cross_page_undo_removes_last_added_not_last_sorted(ui):
    _, functions, state, _ = ui
    state["page"] = 2
    state["selections"] = {"crop": [.1, .1, .9, .9]}
    state, _, _, _ = functions["add_region"](state, state["annotation_context"])
    state["page"] = 1
    state, _, _, _ = functions["add_region"](state, state["annotation_context"])
    assert [region["page_number"] for region in state["regions"]] == [2, 1]
    state, _, _, _ = functions["undo_region"](state, state["annotation_context"])
    assert [region["page_number"] for region in state["regions"]] == [2]


def test_export_cleanup_failure_is_not_claimed_to_be_no_export(ui):
    from ocr_recovery import ReportCleanupError
    app, _, state, _ = ui
    state["references"] = [{"page_number": 1, "reference": "Synthetic"}]
    # Find the reference export handler from its bound input label.
    handler = next(block.fn for block in app.fns.values() if block.fn is not None
                   and any(getattr(component, "label", None) == "Export the stored, reviewed references"
                           for component in block.inputs))
    app._test_export_failure.append(ReportCleanupError("SYNTHETIC_PRIVATE_ERROR"))
    try:
        download, message = handler(True, state, state["annotation_context"])
        assert download is None
        assert "was created" in message
        assert "SYNTHETIC_PRIVATE_ERROR" not in message
    finally:
        app._test_export_failure.clear()


def test_page_navigation_retains_unconfirmed_transcription(ui):
    _, functions, state, _ = ui
    result = functions["change_page"](2, state, "Not yet checked", state["annotation_context"])
    state = result[7]
    assert state["references"] == []
    result = functions["change_page"](1, state, "", state["annotation_context"])
    assert result[4] == "Not yet checked"
    assert result[5] is False


def test_save_draft_does_not_promote_unconfirmed_text(ui):
    _, functions, state, exports = ui
    state, path, message = functions["save_draft"]("Unconfirmed <script>text</script>", state, state["annotation_context"])
    assert path.endswith("synthetic-export.json")
    assert "Unconfirmed" in message
    kind, payload = exports[-1]
    assert kind == "draft"
    assert payload["references"] == []
    assert payload["reference_drafts"][0]["text"] == "Unconfirmed <script>text</script>"
    assert payload["manual_review_required"] is True
    assert state["history"][-1]["action"] == "draft_saved"


def test_failed_page_editor_can_draw_crop_save_draft_and_restore_without_consent(tmp_path):
    pytest.importorskip("gradio")
    Image = pytest.importorskip("PIL.Image")
    from ocr_regions import validate_region_plan
    from ocr_review_drafts import restore_state
    from ocr_review_ui import build_app

    document = ReviewDocument(missing_report(), recovery_sha256="b" * 64)
    exports = []

    def region_plan(regions):
        return validate_region_plan({"schema_version": 1, "source_sha256": document.source_sha256,
                                     "recovery_sha256": document.recovery_sha256,
                                     "coordinate_system": "original_page_display_fraction", "regions": regions},
                                    source_sha256=document.source_sha256, recovery_sha256=document.recovery_sha256,
                                    page_count=document.page_count)

    def save(kind, payload):
        exports.append((kind, copy.deepcopy(payload)))
        return tmp_path / "draft.json"

    workspace = SimpleNamespace(document=document, render=lambda _: Image.new("RGB", (100, 100), "white"),
                                region_plan=region_plan, save=save,
                                # In-memory callbacks use fake exports; real transport is covered separately.
                                prepare_download=lambda path, _cache: path)
    app = build_app(workspace)
    try:
        functions = {block.fn.__name__: block.fn for block in app.fns.values() if block.fn is not None}
        state = copy.deepcopy(next(block.value for block in app.blocks.values() if block.__class__.__name__ == "State"))
        assert state["page"] == 1
        assert document.page(state["page"])["candidate"] is None
        for point in ([10, 20], [90, 80]):
            _, state, _, _ = functions["select_rectangle"](state, "crop", SimpleNamespace(index=point), state["annotation_context"])
        state, _, _, _ = functions["add_region"](state, state["annotation_context"])
        assert state["regions"][0]["bbox"] == [.1, .2, .9, .8]
        with pytest.raises(Exception, match="Review action failed"):
            functions["select_rectangle"](state, "body", SimpleNamespace(index=[0, 0]), state["annotation_context"])
        state, _, _ = functions["save_draft"]("Read from scan, not checked yet", state, state["annotation_context"])
        workspace.initial_draft = restore_state(exports[-1][1], document)
    finally:
        app.close()
    restored_app = build_app(workspace)
    try:
        assert all(block.value is False for block in restored_app.blocks.values()
                   if block.__class__.__name__ == "Checkbox")
        field = next(block for block in restored_app.blocks.values()
                     if getattr(block, "label", None) == "Human-checked reference text for this page")
        assert field.value == "Read from scan, not checked yet"
    finally:
        restored_app.close()
