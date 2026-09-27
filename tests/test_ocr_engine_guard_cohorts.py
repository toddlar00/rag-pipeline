"""Generated PDF cohorts with prepared fake OCR, never model/session loading.

These exercise real page/crop rendering, unchanged report orchestration and
actual-call recording. The fake text is not OCR accuracy evidence.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

import artifact_io
from ocr_engine_limits import EngineAllocationLimit, EngineLimits, image_dimensions, working_dimensions
from ocr_execution_receipt import ExecutionRecorder
import ocr_hardscan_io
import ocr_hardscan_runtime
import ocr_recovery
from ocr_recovery_comparison import validate_recovery_report
import ocr_recovery_runtime
import ocr_region_runtime
import ocr_regions


WORKFLOWS = ("page", "region", "hardscan")
PEER_TEXT = "Prepared fake OCR candidate, not a measured recognition result."


def _candidate(number):
    text = f"Saved synthetic candidate {number}; retained independently of retries."
    return {"text": text, "lines": [{"text": text, "score": .9,
                                      "box": [[0., 0.], [100., 0.], [100., 20.], [0., 20.]]}],
            "mean_confidence": .9,
            "raster": {"width": 300, "height": 300, "dpi": 300,
                       "coordinate_system": "rendered_image_pixels"},
            "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0., "max_side": 6000}}


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    fitz = pytest.importorskip("pymupdf")
    np = pytest.importorskip("numpy")
    actual_snapshot = artifact_io.immutable_file_snapshot

    def snapshot(path, **kwargs):
        return actual_snapshot(path, scratch_root=tmp_path / "scratch", **kwargs)

    monkeypatch.setattr(artifact_io, "immutable_file_snapshot", snapshot)
    import model_artifacts

    def forbid_models(*_args, **_kwargs):
        pytest.fail("cohort test must not verify or load OCR models")

    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", forbid_models)

    def create(workflow, *, thin_pages=(), failure_stage=None, failure=None):
        if workflow == "hardscan":
            pytest.importorskip("cv2")
        source = tmp_path / "generated.pdf"
        originals = []
        with fitz.open() as document:
            for number in (1, 2):
                # Do not rely on MuPDF's sub-point media-box fallback. The
                # actual thin page's admitted source raster is 6000x5 pixels.
                is_thin_page = workflow == "page" and number in thin_pages
                height = 1 if is_thin_page else 72
                width = 1440 if is_thin_page else 72
                page = document.new_page(width=width, height=height)
                assert (page.rect.width, page.rect.height) == (width, height)
                if height == 72:
                    page.insert_text((5, 20), f"Native page {number}", fontsize=6)
                originals.append(page.get_text("text", sort=True))
            source.write_bytes(document.tobytes())
        digest = hashlib.sha256(source.read_bytes()).hexdigest()

        class Baseline:
            page_count = 2

            def native_text(self, number):
                return originals[number - 1]

            def retry(self, number):
                return _candidate(number)

        baseline = ocr_recovery.build_recovery_report(
            Baseline(), source_sha256=digest, policy=ocr_recovery.RetryPolicy(), requested_pages=(1, 2))
        baseline["evidence_sha256"] = None
        baseline_path = tmp_path / "saved-recovery.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        planned = [{"region_id": f"r{number}", "page_number": number,
                    "bbox": [0., 0., 1., .003 if number in thin_pages else 1.]}
                   for number in (1, 2)]
        plan = {"schema_version": 1, "source_sha256": digest,
                "recovery_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                "coordinate_system": "original_page_display_fraction", "regions": planned}
        if workflow == "hardscan":
            plan.update(kind="ocr_hardscan_plan", approval="operator_approved")
            for region in planned:
                region["recipe"] = {"orientation_clockwise": 0, "illumination": "none",
                                    "bow_fraction": 0., "bow_assumption": "none"}
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        inputs = [source, baseline_path, plan_path]
        before = [path.read_bytes() for path in inputs]
        output = tmp_path / "review.json"
        render_pages, load_pages, readers, fired = [], [], [], []
        actual_render = fitz.Page.get_pixmap

        def fail(stage):
            if stage == failure_stage and not fired:
                fired.append(stage)
                raise failure

        def render(page, *args, **kwargs):
            render_pages.append(page.number + 1)
            fail("render")
            return actual_render(page, *args, **kwargs)

        monkeypatch.setattr(fitz.Page, "get_pixmap", render)

        class PreparedEngine:
            def __init__(self):
                self.prepares, self.invocations, self.ready = [], [], None

            def prepare(self, pixels):
                self.prepares.append(pixels.shape)
                assert type(pixels) is np.ndarray and pixels.dtype == np.uint8 and pixels.flags.c_contiguous
                width, height = image_dimensions(pixels.shape, limits=EngineLimits())
                working_dimensions(width, height, detection=True)
                fail("prepare")
                self.ready = pixels

            def __call__(self, pixels):
                assert self.ready is pixels, "the reader must explicitly prepare before its actual call"
                self.ready = None
                self.invocations.append(pixels.shape)
                fail("call")
                height, width = pixels.shape[:2]
                return SimpleNamespace(txts=[PEER_TEXT], scores=[.9],
                                       boxes=[[[0., 0.], [width, 0.], [width, height], [0., height]]])

        fake = PreparedEngine()
        runtime, reader_base = {
            "page": (ocr_recovery_runtime, ocr_recovery_runtime.RapidOCRPageReader),
            "region": (ocr_region_runtime, ocr_region_runtime.RapidOCRRegionReader),
            "hardscan": (ocr_hardscan_runtime, ocr_hardscan_runtime.RapidOCRHardScanReader),
        }[workflow]
        if failure_stage == "preflight":
            actual_working = runtime.working_dimensions

            def preflight(*args, **kwargs):
                fail("preflight")
                return actual_working(*args, **kwargs)

            monkeypatch.setattr(runtime, "working_dimensions", preflight)

        class TestReader(reader_base):
            def __enter__(self):
                super().__enter__()
                readers.append(self)
                for number in (1, 2):
                    expected = (1440, 1) if workflow == "page" and number in thin_pages else (72, 72)
                    rect = self._page(number).rect
                    assert (rect.width, rect.height) == expected
                return self

            def _page(self, number):
                self.active_page = number
                return super()._page(number)

            def _load_engine(self):
                load_pages.append(self.active_page)
                fail("load")
                if self._engine is None:
                    self._engine, self._engine_version = fake, "synthetic"
                return self._engine

            def execution_observation(self):
                # No native sessions exist in this test: do not fabricate their evidence.
                return None

        recorder = ExecutionRecorder()
        reader_factory = recorder.reader_factory(TestReader)

        def run():
            if workflow == "page":
                return ocr_recovery.recover_pdf(source, output, requested_pages=(1, 2), reader_factory=reader_factory)
            operation = ocr_regions.recover_regions if workflow == "region" else ocr_hardscan_io.recover_hardscan
            return operation(source, baseline_path, plan_path, output, reader_factory=reader_factory)

        return SimpleNamespace(run=run, output=output, inputs=inputs, before=before, baseline=baseline,
                               originals=originals, engine=fake, recorder=recorder, readers=readers,
                               renders=render_pages, loads=load_pages, fired=fired)

    return create


def _assert_preserved(case, workflow, result):
    assert [path.read_bytes() for path in case.inputs] == case.before
    assert json.loads(case.output.read_text(encoding="utf-8")) == result
    assert case.readers and all(reader._document is None and reader._engine is None for reader in case.readers)
    assert result["canonical_extraction_modified"] is False
    assert case.recorder.observation is None
    if workflow == "page":
        assert validate_recovery_report(result) == result
        assert [page["original_text"] for page in result["pages"]] == case.originals
    elif workflow == "region":
        assert ocr_regions.load_region_review(case.output) == result
        assert [item["recovery_page"] for item in result["page_contexts"]] == case.baseline["pages"]
    else:
        assert ocr_hardscan_io.load_report(case.output) == result
        assert result["baseline_recovery"] == case.baseline


def _assert_outcomes(result, workflow, statuses):
    items = result["pages" if workflow == "page" else "regions"]
    assert [item["status"] for item in items] == statuses
    for item, status in zip(items, statuses):
        if status == "retry_failed":
            assert item["error_code"] == "retry_limit_or_validation" and item["candidate"] is None
        else:
            assert item["error_code"] is None and item["candidate"]["text"] == PEER_TEXT
    return items


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_thin_preflight_is_local_and_skips_model_render_and_call_before_normal_peer(scenario, workflow):
    case = scenario(workflow, thin_pages=(1,))
    result = case.run()
    items = _assert_outcomes(result, workflow, ["retry_failed", "review_required"])
    if workflow != "page":
        assert items[0]["geometry"]["raster"]["height"] == 1
    assert case.loads == case.renders == [2]
    assert case.engine.prepares == case.engine.invocations == [(300, 300, 3)]
    assert [call["status"] for call in case.recorder.calls] == ["completed"]
    _assert_preserved(case, workflow, result)


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_all_thin_preflight_failures_publish_explicit_outcomes_with_zero_engine_work(scenario, workflow):
    case = scenario(workflow, thin_pages=(1, 2))
    result = case.run()
    _assert_outcomes(result, workflow, ["retry_failed", "retry_failed"])
    assert case.loads == case.renders == case.engine.prepares == case.engine.invocations == case.recorder.calls == []
    _assert_preserved(case, workflow, result)


@pytest.mark.parametrize("workflow", WORKFLOWS)
@pytest.mark.parametrize("stage", ["prepare", "call"])
@pytest.mark.parametrize("error_type", [ValueError, EngineAllocationLimit])
def test_individual_validation_failure_preserves_peer_and_actual_invocation_count(scenario, workflow, stage, error_type):
    case = scenario(workflow, failure_stage=stage, failure=error_type("synthetic validation refusal"))
    result = case.run()
    _assert_outcomes(result, workflow, ["retry_failed", "review_required"])
    assert case.loads == case.renders == [1, 2]
    assert len(case.engine.prepares) == 2
    expected = ["completed"] if stage == "prepare" else ["failed", "completed"]
    assert len(case.engine.invocations) == len(expected)
    assert [call["status"] for call in case.recorder.calls] == expected
    assert [call["id"] for call in case.recorder.calls] == [f"call-{i:04d}" for i in range(1, len(expected) + 1)]
    _assert_preserved(case, workflow, result)


@pytest.mark.parametrize("workflow", WORKFLOWS)
@pytest.mark.parametrize("stage", ["preflight", "load", "render", "prepare", "call"])
@pytest.mark.parametrize("cancel_type", [KeyboardInterrupt, SystemExit])
def test_cancellation_stops_cohort_and_does_not_publish_or_invent_calls(scenario, workflow, stage, cancel_type):
    cancellation = cancel_type("synthetic cancellation")
    case = scenario(workflow, failure_stage=stage, failure=cancellation)
    with pytest.raises(cancel_type) as caught:
        case.run()
    assert caught.value is cancellation and case.fired == [stage]
    assert not case.output.exists()
    assert [path.read_bytes() for path in case.inputs] == case.before
    assert case.readers and all(reader._document is None and reader._engine is None for reader in case.readers)
    assert 2 not in case.loads and 2 not in case.renders
    expected = ["failed"] if stage == "call" else []
    assert [call["status"] for call in case.recorder.calls] == expected
    assert len(case.engine.invocations) == len(expected)
    assert case.recorder.observation is None
