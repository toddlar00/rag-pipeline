"""Create-only bundles, request binding and publication failures without OCR imports."""

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import ocr_execution_io as workflow
import ocr_recovery
import ocr_recovery_runtime
import storage_policy
from tools import run_ocr_experiment as cli


class Reader:
    page_count = 2

    def __init__(self, _path, **options):
        self._engine = None
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        pass

    def native_text(self, _page):
        return ""

    def retry(self, _page):
        return {"text": "PRIVATE synthetic prediction", "lines": [{"text": "PRIVATE synthetic prediction", "score": .9,
                 "box": [[0., 0.], [100., 0.], [100., 20.], [0., 20.]]}], "mean_confidence": .9,
                "raster": {"width": 100, "height": 100, "dpi": self.options["dpi"], "coordinate_system": "rendered_image_pixels"},
                "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0., "max_side": self.options["max_side"]}}


@pytest.fixture
def files(tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "bundle"
    source.write_bytes(b"synthetic PDF stand-in")
    monkeypatch.setattr(ocr_recovery_runtime, "RapidOCRPageReader", Reader)
    return source, output


def readback(files, result):
    return workflow.read_execution_completion(files[1], inputs=result["manifest"]["inputs"],
                                              configuration=result["manifest"]["configuration"], pdf_path=files[0])


def rewrite_bundle(files, result, *, receipt=None, report=None, manifest=None):
    value = copy.deepcopy(result["manifest"])
    for payload, filename, field in ((receipt, "execution.json", "execution_sha256"), (report, "report.json", "report_sha256")):
        if payload is not None:
            data = json.dumps(payload).encode()
            (files[1] / filename).write_bytes(data)
            value[field] = hashlib.sha256(data).hexdigest()
    if manifest:
        manifest(value)
    (files[1] / "manifest.json").write_text(json.dumps(value), encoding="utf-8")


def test_bundle_preserves_report_schema_and_has_private_content_free_receipt(files):
    result = workflow.run_execution_bundle(*files)
    assert readback(files, result) == result["manifest"]
    assert {p.name for p in files[1].iterdir() if p.is_file()} == {"report.json", "execution.json", "manifest.json"}
    assert {p.name for p in files[1].iterdir() if p.is_dir()} <= {".rag-locks"}
    assert result["report"]["schema_version"] == 1
    assert not result["receipt"]["ocr_executed"]  # Fake reader never called an engine.
    assert "PRIVATE" not in json.dumps(result["receipt"]) + json.dumps(result["manifest"])
    assert "PRIVATE" in json.dumps(result["report"])
    for key in ("model_policy_sha256", "model_lock_sha256", "dependency_full_sha256", "configuration_sha256"):
        assert len(result["manifest"]["inputs"][key]) == 64
    assert files[0].read_bytes() == b"synthetic PDF stand-in"
    if os.name != "nt":
        assert files[1].stat().st_mode & 0o777 == 0o700
        assert (files[1] / "execution.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kwargs", [{"policy": {}}, {"policy": False}, {"requested_pages": [1]},
                                     {"requested_pages": (True,)}, {"requested_pages": (0,)}, {"requested_pages": (5001,)},
                                     {"requested_pages": (1, 1)}, {"requested_pages": tuple(range(1, 22))},
                                     {"operation": "PRIVATE"}, {"recovery_path": Path("PRIVATE")}])
def test_invalid_request_does_not_create_directory(files, kwargs):
    with pytest.raises(ValueError) as error:
        workflow.run_execution_bundle(*files, **kwargs)
    assert not files[1].exists() and "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
@pytest.mark.parametrize("policy", [ocr_recovery.RetryPolicy(max_pages=1), ocr_recovery.RetryPolicy(preprocessing="contrast"),
                                    ocr_recovery.RetryPolicy(min_chars=1), ocr_recovery.RetryPolicy(max_side=100)])
def test_crop_request_never_records_ignored_page_settings(files, operation, policy):
    with pytest.raises(ValueError):
        workflow.run_execution_bundle(*files, operation=operation, policy=policy,
                                      recovery_path=files[0], plan_path=files[0])
    assert not files[1].exists()


def test_alias_inputs_and_existing_directory_are_not_overwritten(files):
    with pytest.raises(ValueError):
        workflow.run_execution_bundle(*files, evidence_path=files[0])
    linked = files[0].with_name("linked")
    os.link(files[0], linked)
    with pytest.raises(ValueError):
        workflow.run_execution_bundle(*files)
    linked.unlink()
    files[1].mkdir()
    sentinel = files[1] / "user-file"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(ValueError):
        workflow.run_execution_bundle(*files)
    assert sentinel.read_bytes() == b"preserve"


def test_symlink_output_is_rejected(files):
    try:
        files[1].symlink_to(files[0])
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises((ValueError, OSError)):
        workflow.run_execution_bundle(*files)


@pytest.mark.parametrize("stage,target", [("execution.json", "source.pdf"), ("manifest.json", "source.pdf"),
                                         ("manifest.json", "report.json"), ("manifest.json", "execution.json")])
def test_changed_input_or_result_prevents_completion_marker(files, monkeypatch, stage, target):
    original = storage_policy.atomic_write_private_json
    def write(path, payload, **kwargs):
        if Path(path).name == stage:
            changed = files[0] if target == "source.pdf" else files[1] / target
            changed.write_bytes(b"changed synthetic data")
        return original(path, payload, **kwargs)
    monkeypatch.setattr(storage_policy, "atomic_write_private_json", write)
    with pytest.raises((ValueError, RuntimeError)):
        workflow.run_execution_bundle(*files)
    assert files[1].is_dir() and not (files[1] / "manifest.json").exists()


def test_publication_race_keeps_uncooperative_writer_file(files, monkeypatch):
    original = storage_policy.atomic_write_private_json
    def write(path, payload, **kwargs):
        if Path(path).name == "execution.json":
            Path(path).write_bytes(b"user file")
        return original(path, payload, **kwargs)
    monkeypatch.setattr(storage_policy, "atomic_write_private_json", write)
    with pytest.raises(FileExistsError):
        workflow.run_execution_bundle(*files)
    assert (files[1] / "execution.json").read_bytes() == b"user file"
    assert not (files[1] / "manifest.json").exists()


@pytest.mark.parametrize("mutate", [lambda v: v.update(schema_version=True),
                                    lambda v: v.update(extra="PRIVATE"),
                                    lambda v: v["configuration"]["policy"].update(max_pages=True),
                                    lambda v: v["configuration"]["policy"].update(dpi=300.),
                                    lambda v: v["inputs"].update(source_sha256="f" * 64)])
def test_type_strict_manifest_readback(files, mutate):
    result = workflow.run_execution_bundle(*files)
    rewrite_bundle(files, result, manifest=mutate)
    with pytest.raises(ValueError):
        readback(files, result)


@pytest.mark.parametrize("mutate", [lambda v: v.update(schema_version=True), lambda v: v.update(ocr_executed=True),
                                    lambda v: v["summary"].update(attempted=1),
                                    lambda v: v["runtime"].update(locked_environment_verified=True),
                                    lambda v: v["runtime"].update(package_versions_match=not v["runtime"]["package_versions_match"]),
                                    lambda v: v.update(engine_observation={"PRIVATE": "private"}),
                                    lambda v: v.update(installation_evidence="verified_local_bindings"),
                                    lambda v: v["runtime"]["packages"][0].update(loaded_version_agrees=not v["runtime"]["packages"][0]["loaded_version_agrees"])])
def test_digest_consistent_forged_receipt_cannot_claim_success(files, mutate):
    result = workflow.run_execution_bundle(*files)
    changed = copy.deepcopy(result["receipt"])
    mutate(changed)
    rewrite_bundle(files, result, receipt=changed)
    with pytest.raises(ValueError):
        readback(files, result)


@pytest.mark.parametrize("mutate", [lambda v: v["selection"].update(max_pages=1),
                                    lambda v: v["retry_configuration"].update(dpi=400),
                                    lambda v: v.update(source_sha256="a" * 64),
                                    lambda v: v["summary"].update(retried=True)])
def test_digest_consistent_report_policy_mismatch_is_rejected(files, mutate):
    result = workflow.run_execution_bundle(*files)
    changed_report = copy.deepcopy(result["report"])
    mutate(changed_report)
    data = json.dumps(changed_report).encode()
    changed_receipt = copy.deepcopy(result["receipt"])
    changed_receipt["output_sha256"] = hashlib.sha256(data).hexdigest()
    rewrite_bundle(files, result, report=changed_report, receipt=changed_receipt)
    with pytest.raises(ValueError):
        readback(files, result)


def test_report_size_limits_match_existing_workflow_contracts():
    import ocr_regions
    import ocr_hardscan_io
    assert workflow._report_limit("pages") == 64 * 1024 * 1024
    assert workflow._report_limit("regions") == ocr_regions.MAX_REPORT_BYTES
    assert workflow._report_limit("hardscan") == ocr_hardscan_io.MAX_REPORT_BYTES


def test_cli_contains_quiet_child_and_validates_real_bundle(files, monkeypatch, capsys):
    import process_supervision
    def supervise(_path, arguments, **kwargs):
        assert arguments[-1] == "--worker"
        assert kwargs["stdout_target"] == kwargs["stderr_target"] == cli.subprocess.DEVNULL
        assert kwargs["environment_overrides"]["HF_HUB_OFFLINE"] == "1"
        workflow.run_execution_bundle(*files)
        return 3
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    assert cli.main(["--pdf", str(files[0]), "--output-dir", str(files[1])]) == 3
    output = capsys.readouterr()
    assert "created" in output.out and "PRIVATE" not in output.out + output.err


@pytest.mark.parametrize("code", [2, 0, 124, 130])
def test_cli_noncompletion_never_claims_no_output_or_success(files, monkeypatch, capsys, code):
    import process_supervision
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", lambda *_args, **_kwargs: code)
    result = cli.main(["--pdf", str(files[0]), "--output-dir", str(files[1])])
    assert result == (code if code in (124, 130) else 2)
    output = capsys.readouterr()
    assert "may remain" in output.err and "created" not in output.out


def test_cli_parent_rejects_post_worker_input_change(files, monkeypatch, capsys):
    import process_supervision
    def supervise(*_args, **_kwargs):
        workflow.run_execution_bundle(*files)
        files[0].write_bytes(b"changed")
        return 3
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    assert cli.main(["--pdf", str(files[0]), "--output-dir", str(files[1])]) == 2
    assert "may remain" in capsys.readouterr().err


def test_cli_worker_requires_gate_and_errors_are_static(files, monkeypatch, capsys):
    monkeypatch.delenv("RAG_OCR_EXECUTION_CHILD", raising=False)
    assert cli.main(["--pdf", "PRIVATE", "--output-dir", str(files[1]), "--worker"]) == 2
    assert "PRIVATE" not in capsys.readouterr().err


def test_cli_late_readback_cancellation_is_honest(files, monkeypatch, capsys):
    import process_supervision
    def supervise(*_args, **_kwargs):
        workflow.run_execution_bundle(*files)
        return 3
    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(process_supervision, "_run_cli_with_deadline", supervise)
    monkeypatch.setattr(workflow, "read_execution_completion", cancel)
    assert cli.main(["--pdf", str(files[0]), "--output-dir", str(files[1])]) == 130
    assert (files[1] / "manifest.json").is_file()
    assert "may remain" in capsys.readouterr().err


@pytest.mark.parametrize("operation", ["regions", "hardscan"])
def test_crop_bundle_full_report_and_context_readback(files, monkeypatch, operation):
    import ocr_region_runtime
    import ocr_hardscan
    import ocr_hardscan_runtime
    saved_path, plan_path = files[0].with_name("saved.json"), files[0].with_name("plan.json")
    saved = ocr_recovery.build_recovery_report(Reader(None, dpi=300, max_side=6000),
                                               source_sha256=hashlib.sha256(files[0].read_bytes()).hexdigest(),
                                               policy=ocr_recovery.RetryPolicy())
    saved["evidence_sha256"] = None
    saved_path.write_text(json.dumps(saved), encoding="utf-8")
    plan = {"schema_version": 1, "source_sha256": saved["source_sha256"],
            "recovery_sha256": hashlib.sha256(saved_path.read_bytes()).hexdigest(),
            "coordinate_system": "original_page_display_fraction",
            "regions": [{"region_id": "r1", "page_number": 1, "bbox": [0., 0., 1., 1.]}]}
    if operation == "hardscan":
        plan.update(kind="ocr_hardscan_plan", approval="operator_approved")
        plan["regions"][0]["recipe"] = {"orientation_clockwise": 0, "bow_fraction": 0.,
                                          "illumination": "none", "bow_assumption": "none"}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    class CropReader(Reader):
        def describe_region(self, _number, _bbox):
            dpi = self.options["dpi"]
            return {"page": {"display_rect_points": [0., 0., 72., 72.], "cropbox_points": [0., 0., 72., 72.], "rotation_degrees": 0},
                    "raster": {"width": dpi, "height": dpi, "dpi": dpi, "coordinate_system": "rendered_image_pixels"},
                    "pixel_bounds": [0, 0, dpi, dpi], "crop_to_page_fraction": [[1/dpi, 0., 0.], [0., 1/dpi, 0.]],
                    "boundary_policy": "clip_to_page_bounds"}
        def describe_hardscan(self, number, bbox, recipe):
            geometry = self.describe_region(number, bbox)
            return {"geometry": geometry, "transform": ocr_hardscan.describe_transform(self.options["dpi"], self.options["dpi"], recipe)}
        def retry_region(self, *_args):
            raise ImportError("PRIVATE model unavailable")
        def retry_hardscan(self, *_args):
            raise ImportError("PRIVATE model unavailable")
    monkeypatch.setattr(ocr_region_runtime, "RapidOCRRegionReader", CropReader)
    monkeypatch.setattr(ocr_hardscan_runtime, "RapidOCRHardScanReader", CropReader)
    result = workflow.run_execution_bundle(*files, operation=operation, recovery_path=saved_path, plan_path=plan_path)
    assert result["report"]["regions"][0]["status"] == "retry_failed"
    assert result["receipt"]["summary"]["attempted"] == 0 and not result["receipt"]["ocr_executed"]
    assert result["manifest"]["configuration"]["policy"] == {"dpi": 300}
    def check():
        return workflow.read_execution_completion(files[1], inputs=result["manifest"]["inputs"],
                                                   configuration=result["manifest"]["configuration"], pdf_path=files[0],
                                                   recovery_path=saved_path, plan_path=plan_path)
    assert check() == result["manifest"]
    saved["pages"][0]["candidate"]["lines"][0]["text"] = "different saved context"
    saved["pages"][0]["candidate"]["text"] = "different saved context"
    saved_path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError):
        check()


def test_cli_help_has_no_heavy_imports():
    import subprocess
    import sys
    source = "from tools.run_ocr_experiment import main\nimport sys\ntry: main(['--help'])\nexcept SystemExit: pass\nassert not {'cv2','numpy','fitz','rapidocr','onnxruntime'} & sys.modules.keys()"
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, check=False)
    assert result.returncode == 0
