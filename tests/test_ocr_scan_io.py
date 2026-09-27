"""Synthetic scan observations and real strict private IO; no native work."""

import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS

import pytest

import ocr_scan_io as io
import ocr_scan_omission as policy
from ocr_recovery import ReportCleanupError
from resource_lease import PathLease, PathLeaseBusyError
import storage_policy
from test_ocr_docling import PRIVATE, candidate, document, recovery
import ocr_docling


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_metadata(lock_digest="a" * 64):
    return {"schema_version": 1, "kind": "ocr_experiment_runtime", "lock_sha256": lock_digest,
        "environment": {"python_full_version": "3.12.10", "python_version": "3.12", "os_name": "nt",
                        "sys_platform": "win32", "platform_machine": "AMD64", "platform_python_implementation": "CPython"},
        "packages": [{"name": name, "required_version": version, "installed_version": version,
                      "status": "version_match", "loaded_versions": [loaded], "loaded_version_agrees": True}
                     for name, version, loaded in (("numpy", "2.4.2", "2.4.2"),
                         ("opencv-python", "5.0.0.93", "5.0.0"), ("pymupdf", "1.27.2", "1.27.2"))],
        "version_check_scope": "requested_packages_only", "package_versions_match": True,
        "effective_runtime": None, "effective_runtime_source": None, "locked_environment_verified": False,
        "artifact_verification": {"package_wheels": "not_verified", "model_files": "not_verified_by_capture",
                                  "loaded_native_libraries": "not_verified"}, "requires_attention": True}


def observation(source_digest, requested, *, page_count=1, recipe="legacy-v1"):
    value = {"schema_version": 1 if recipe == "legacy-v1" else 2,
        "kind": "ocr_scan_observation", "source_sha256": source_digest,
        "page_count": page_count, "requested_pages": list(requested),
        "configuration": policy.configuration_for_recipe(recipe),
        "pages": [{"page_number": number, "geometry": None, "raster": None,
            "discovery": {"status": "unavailable", "reason": "geometry_unavailable", "threshold": None,
                "threshold_foreground_pixels": None, "foreground_accounting_complete": False, "regions": [],
                "work": {"rule_horizontal_runs": None, "residual_horizontal_runs": None,
                         "rule_component_count": None, "residual_component_count": None, "pair_tests": 0}}}
                  for number in requested]}
    if recipe == "spatial-v2":
        for page in value["pages"]:
            page["discovery"]["work"].update(glyph_count=None, spatial_phase="not_started",
                spatial_index_entries=0, spatial_bucket_lookups=0, spatial_bucket_visits=0)
    return value


@pytest.fixture
def files(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in (*io._LOCKS, *io._PRODUCERS):
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"Synthetic producer or lock: " + name.encode())
    source, saved, proposals, installation, output = (
        tmp_path / name for name in ("PRIVATE-source.pdf", "recovery.json", "proposals.json", "installation.json", "bundle"))
    source.write_bytes(b"Synthetic non-PDF input, never parsed or rendered.")
    installation.write_bytes(b'{"synthetic_installation":true}')
    report = recovery([candidate()], source=digest(source))
    saved.write_text(json.dumps(report), encoding="utf-8")
    proposed = ocr_docling.build_docling_proposals(document(), report, recovery_sha256=digest(saved),
        docling_sha256="c" * 64, manifest_sha256="d" * 64, effective_input_kind="original")
    proposals.write_text(json.dumps(proposed), encoding="utf-8")
    monkeypatch.setattr(io, "ROOT", repo)
    monkeypatch.setattr(io, "_runtime_metadata", lambda: native_metadata(digest(repo / io._LOCKS[0])))
    seen = []
    state = NS(source=source, saved=saved, proposals=proposals, installation=installation, output=output,
               repo=repo, seen=seen, recipes=[], page_count=1, mutate=lambda value: None)
    def collect(raw, *, requested_pages, recipe="legacy-v1"):
        seen.append((raw, list(requested_pages)))
        state.recipes.append(recipe)
        value = observation(hashlib.sha256(raw).hexdigest(), requested_pages, page_count=state.page_count, recipe=recipe)
        state.mutate(value)
        return value
    monkeypatch.setitem(sys.modules, "ocr_scan_runtime", NS(collect_scan_observation=collect))
    monkeypatch.setattr(io, "validate_installation_evidence", lambda path: ({}, digest(path)))
    return state


def request(files, **kwargs):
    return io.scan_request(files.source, files.output, requested_pages=[1], **kwargs)


def run(files, **kwargs):
    return io.run_scan_bundle(files.source, files.output, requested_pages=[1], **kwargs)


def rebound(files, filename, mutate):
    path = files.output / filename
    value = json.loads(path.read_bytes())
    mutate(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    if filename != "manifest.json":
        manifest = files.output / "manifest.json"
        value = json.loads(manifest.read_bytes())
        value[filename.removesuffix(".json") + "_sha256"] = digest(path)
        manifest.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("mode", ["source_only", "recovery", "proposals", "installation"])
def test_real_strict_bundle_no_clobber_private_content_free_readback(files, mode):
    options = {"recovery_path": files.saved} if mode in {"recovery", "proposals"} else {}
    if mode == "proposals":
        options["proposals_path"] = files.proposals
    if mode == "installation":
        options["installation_path"] = files.installation
    expected = request(files, **options)
    before = files.source.read_bytes()
    result = run(files, **options)
    assert io.read_scan_completion(files.output, request=expected) == result
    assert files.seen == [(before, [1])] and files.source.read_bytes() == before
    assert {p.name for p in files.output.iterdir() if p.is_file()} == {"scan.json", "diagnostics.json", "manifest.json"}
    assert result["diagnostics"]["summary"]["unavailable_pages"] == 1
    assert result["diagnostics"]["accuracy_verified"] is False
    text = json.dumps(result)
    assert PRIVATE not in text and "PRIVATE-source" not in text and str(files.source) not in text
    assert result["manifest"]["installation_evidence"] == ("verified_local_bindings" if mode == "installation" else "not_supplied")
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(files.output, directory=True)
        assert storage_policy.windows_path_is_private(files.output / "manifest.json", directory=False)
    else:
        assert files.output.stat().st_mode & 0o777 == 0o700
        assert (files.output / "manifest.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        run(files, **options)
    later = io.scan_request(files.source, files.output, requested_pages=[1], readback=True, **options)
    assert io.read_scan_completion(files.output, request=later) == result


def test_requested_cohort_detached_and_unavailable_coverage_preserved(files):
    files.page_count = 8
    pages = [6, 1]
    expected = io.scan_request(files.source, files.output, requested_pages=pages)
    pages[:] = [8]
    with pytest.raises(FrozenInstanceError):
        expected.requested_pages = (8,)
    result = io.run_scan_bundle(files.source, files.output, requested_pages=[6, 1])
    assert result["scan"]["requested_pages"] == [1, 6]
    assert result["diagnostics"]["coverage"]["unrequested_pages"] == [2, 3, 4, 5, 7, 8]
    assert io.read_scan_completion(files.output, request=expected) == result


@pytest.mark.parametrize("value", [None, (), [], [True], [0], [5001], [1, 1], list(range(1, 10)), [1.0]])
def test_bad_pages_never_create_bundle_or_enter_native(files, value):
    with pytest.raises(ValueError):
        io.run_scan_bundle(files.source, files.output, requested_pages=value)
    assert not files.output.exists() and not files.seen


def test_optional_proposals_require_recovery_before_native(files):
    with pytest.raises(ValueError):
        run(files, proposals_path=files.proposals)
    assert not files.output.exists() and not files.seen


@pytest.mark.parametrize("target", ["source", "saved", "proposals", "installation"])
@pytest.mark.parametrize("same_bytes", [False, True])
def test_each_input_byte_or_identity_replacement_rejected_on_parent_readback(files, target, same_bytes):
    options = {"recovery_path": files.saved, "proposals_path": files.proposals, "installation_path": files.installation}
    expected = request(files, **options)
    run(files, **options)
    path = getattr(files, target)
    content = path.read_bytes()
    if same_bytes:
        path.rename(path.with_suffix(".prior"))
    path.write_bytes(content if same_bytes else content + b" ")
    with pytest.raises((ValueError, RuntimeError)):
        io.read_scan_completion(files.output, request=expected)


@pytest.mark.parametrize("name", ["requirements-full.lock", "ocr_scan_runtime.py", "ocr_scan_omission.py", "tools/inspect_ocr_scan.py"])
def test_lock_or_scoped_producer_change_blocks_readback(files, name):
    expected = request(files)
    run(files)
    (files.repo / name).write_bytes(b"changed synthetic generation")
    with pytest.raises((ValueError, RuntimeError)):
        io.read_scan_completion(files.output, request=expected)


@pytest.mark.parametrize("filename,target", [("scan.json", "source"), ("diagnostics.json", "saved"),
    ("manifest.json", "proposals"), ("manifest.json", "installation"),
    ("manifest.json", "scan.json"), ("manifest.json", "diagnostics.json")])
def test_final_publication_rechecks_all_inputs_and_already_published_outputs(files, monkeypatch, filename, target):
    actual = storage_policy.atomic_write_private
    def write(path, writer, **options):
        commit = options["replace_fn"]
        def change(temporary, destination):
            if Path(path).name == filename:
                changed = files.output / target if target.endswith(".json") else getattr(files, target)
                changed.write_bytes(changed.read_bytes() + b" ")
            commit(temporary, destination)
        return actual(path, writer, **{**options, "replace_fn": change})
    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises((ValueError, RuntimeError)):
        run(files, recovery_path=files.saved, proposals_path=files.proposals, installation_path=files.installation)
    assert not (files.output / "manifest.json").exists()


@pytest.mark.parametrize("filename", ["scan.json", "diagnostics.json", "manifest.json"])
def test_staged_tamper_never_publishes_completed_manifest(files, monkeypatch, filename):
    actual = storage_policy.atomic_write_private
    def write(path, writer, **options):
        commit = options["replace_fn"]
        def change(temporary, destination):
            if Path(path).name == filename:
                Path(temporary).write_bytes(b'{"wrong":true}')
            commit(temporary, destination)
        return actual(path, writer, **{**options, "replace_fn": change})
    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises(RuntimeError):
        run(files)
    assert not (files.output / "manifest.json").exists()


@pytest.mark.parametrize("mutate", [lambda m: m.update(schema_version=True),
    lambda m: m.update(recognition_rerun=True), lambda m: m.update(requires_attention=False),
    lambda m: m.update(installation_evidence="verified_local_bindings"),
    lambda m: m["request"]["configuration"].update(dpi=300.0),
    lambda m: m["request"].update(requested_pages=[2]),
    lambda m: m.update(request_sha256="0" * 64),
    lambda m: m.update(extra="PRIVATE")])
def test_manifest_type_and_request_forgeries_fail_closed(files, mutate):
    expected = request(files)
    run(files)
    rebound(files, "manifest.json", mutate)
    with pytest.raises(ValueError):
        io.read_scan_completion(files.output, request=expected)


@pytest.mark.parametrize("mutate", [lambda s: s.update(source_sha256="0" * 64),
    lambda s: s.update(requested_pages=[2], pages=[]), lambda s: s["configuration"].update(dpi=300.0)])
def test_bad_native_return_cannot_publish(files, mutate):
    files.mutate = mutate
    with pytest.raises(ValueError):
        run(files)
    assert not (files.output / "manifest.json").exists()


def test_rebound_diagnostics_are_recomputed_not_trusted(files):
    expected = request(files)
    run(files)
    rebound(files, "diagnostics.json", lambda value: value["summary"].update(unavailable_pages=0))
    with pytest.raises(ValueError):
        io.read_scan_completion(files.output, request=expected)


@pytest.mark.parametrize("mutate", [lambda r: r.update(elapsed_seconds=True),
    lambda r: r.update(elapsed_seconds=float("inf")), lambda r: r.update(elapsed_seconds=-1),
    lambda r: r["packages"]["packages"][0].update(loaded_versions=[], loaded_version_agrees=None),
    lambda r: r["packages"]["packages"][0].update(loaded_versions=["9.9"], loaded_version_agrees=True),
    lambda r: r["packages"]["packages"][0].update(loaded_version_agrees=1),
    lambda r: r["packages"].update(locked_environment_verified=True),
    lambda r: r.update(extra=True)])
def test_loaded_runtime_claims_cannot_be_forged_with_rebound_manifest(files, mutate):
    expected = request(files)
    run(files)
    rebound(files, "manifest.json", lambda value: mutate(value["runtime"]))
    with pytest.raises((ValueError, TypeError)):
        io.read_scan_completion(files.output, request=expected)


def test_real_opencv_distribution_suffix_is_allowed(files):
    result = run(files)
    cv = next(p for p in result["manifest"]["runtime"]["packages"]["packages"] if p["name"] == "opencv-python")
    assert cv["required_version"] == "5.0.0.93" and cv["loaded_versions"] == ["5.0.0"]


def test_lock_check_requests_only_model_free_packages(monkeypatch):
    calls = []
    monkeypatch.setattr(io, "capture_runtime_manifest", lambda *a, **k: calls.append((a, k)))
    io._runtime_metadata()
    assert calls == [((io.ROOT / "requirements-full.lock",),
                     {"packages": ("pymupdf", "opencv-python", "numpy"), "require_version_match": True})]


@pytest.mark.parametrize("target", ["source", "saved", "proposals"])
def test_output_input_aliases_and_existing_paths_refused(files, target):
    with pytest.raises(ValueError):
        io.run_scan_bundle(files.source, getattr(files, target), requested_pages=[1])
    assert not files.seen


def test_missing_parent_and_readback_of_absent_bundle_refused(files):
    with pytest.raises(ValueError):
        request(files, readback=True)
    with pytest.raises((ValueError, FileNotFoundError)):
        io.run_scan_bundle(files.source, files.output / "nested", requested_pages=[1])
    assert not files.output.exists() and not files.seen


def test_input_hardlinks_and_symlinks_rejected(files, tmp_path):
    alias = tmp_path / "alias"
    os.link(files.source, alias)
    with pytest.raises(ValueError):
        run(files)
    alias.unlink()
    alias.symlink_to(files.source)
    with pytest.raises((OSError, ValueError)):
        io.run_scan_bundle(alias, files.output, requested_pages=[1])
    assert not files.output.exists() and not files.seen


@pytest.mark.parametrize("error", [KeyboardInterrupt(), ReportCleanupError("synthetic cleanup")])
def test_late_cancel_or_cleanup_retains_complete_report_honestly(files, monkeypatch, error):
    original = io._publish_new_report
    def publish(temporary, destination):
        original(temporary, destination)
        if Path(destination).name == "manifest.json":
            raise error
    monkeypatch.setattr(io, "_publish_new_report", publish)
    with pytest.raises(type(error)):
        run(files)
    assert (files.output / "manifest.json").exists()


def test_cancel_before_native_return_leaves_only_incomplete_directory(files):
    def cancel(_value):
        raise KeyboardInterrupt()
    files.mutate = cancel
    with pytest.raises(KeyboardInterrupt):
        run(files)
    assert files.output.is_dir() and not (files.output / "manifest.json").exists()


def test_uncooperative_output_writer_is_never_overwritten(files, monkeypatch):
    original = io._publish_new_report
    def publish(temporary, destination):
        Path(destination).write_bytes(b"other writer")
        original(temporary, destination)
    monkeypatch.setattr(io, "_publish_new_report", publish)
    with pytest.raises(FileExistsError):
        run(files)
    assert (files.output / "scan.json").read_bytes() == b"other writer"
    assert not (files.output / "manifest.json").exists()


def test_snapshot_exact_limit_and_oversize(files):
    size = files.source.stat().st_size
    assert io._snapshot(files.source, size)[0] == files.source.read_bytes()
    with pytest.raises(ValueError):
        io._snapshot(files.source, size - 1)


def test_mutated_frozen_request_rejected_without_native_work(files):
    expected = request(files)
    bad = replace(expected, requested_pages=(2,))
    with pytest.raises(RuntimeError):
        io.recheck_scan_request(bad)
    assert not files.seen


@pytest.mark.parametrize("filename", ["scan.json", "diagnostics.json", "manifest.json"])
def test_each_serialized_artifact_limit_precedes_publication(files, monkeypatch, filename):
    monkeypatch.setitem(io._LIMITS, filename, 20)
    with pytest.raises(ValueError):
        run(files)
    assert not (files.output / filename).exists()
    assert not (files.output / "manifest.json").exists()


def test_actual_page_count_must_match_optional_recovery(files):
    files.page_count = 2
    with pytest.raises(ValueError):
        run(files, recovery_path=files.saved)
    assert not (files.output / "manifest.json").exists()


def test_wrong_source_in_valid_recovery_is_rejected_before_native(files):
    value = json.loads(files.saved.read_bytes())
    value["source_sha256"] = "f" * 64
    files.saved.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        run(files, recovery_path=files.saved)
    assert not files.output.exists() and not files.seen


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{"PRIVATE":'])
def test_strict_optional_json_failure_precedes_native(files, raw):
    files.saved.write_bytes(raw)
    with pytest.raises(ValueError):
        run(files, recovery_path=files.saved)
    assert not files.output.exists() and not files.seen


def test_parent_directory_generation_is_checked_before_native(files, monkeypatch):
    expected = request(files)
    original = io._directory
    def changed(path):
        identity = original(path)
        return (identity[0], identity[1] + 1) if path == files.output.parent else identity
    monkeypatch.setattr(io, "_directory", changed)
    with pytest.raises(RuntimeError):
        io.recheck_scan_request(expected)
    assert not files.seen


def test_actual_bundle_directory_replacement_during_commit_is_rejected(files, monkeypatch):
    original = storage_policy.atomic_write_private
    moved = files.output.with_name("preserved-original-bundle")
    def write(path, writer, **options):
        commit = options["replace_fn"]
        def replace_directory(temporary, destination):
            if Path(path).name == "manifest.json":
                files.output.rename(moved)
                files.output.mkdir()
            commit(temporary, destination)
        return original(path, writer, **{**options, "replace_fn": replace_directory})
    monkeypatch.setattr(storage_policy, "atomic_write_private", write)
    with pytest.raises((RuntimeError, OSError)):
        run(files)
    assert not (files.output / "manifest.json").exists()
    assert not (moved / "manifest.json").exists()


def test_report_changes_after_final_input_recheck_are_caught(files, monkeypatch):
    expected = request(files)
    run(files)
    original = io.recheck_scan_request
    calls = 0
    def recheck(request):
        nonlocal calls
        original(request)
        calls += 1
        if calls == 2:
            path = files.output / "scan.json"
            path.write_bytes(path.read_bytes() + b" ")
    monkeypatch.setattr(io, "recheck_scan_request", recheck)
    with pytest.raises(RuntimeError):
        io.read_scan_completion(files.output, request=expected)


def test_discovery_argument_mutation_cannot_change_frozen_requested_cohort(files, monkeypatch):
    def collect(raw, *, requested_pages):
        requested_pages[:] = [2]
        return observation(hashlib.sha256(raw).hexdigest(), [2], page_count=2)
    monkeypatch.setattr(sys.modules["ocr_scan_runtime"], "collect_scan_observation", collect)
    with pytest.raises(ValueError):
        run(files)
    assert not (files.output / "manifest.json").exists()


def test_derived_builder_cannot_mutate_observed_scan_or_comparison_context(files, monkeypatch):
    original = policy.build_scan_omission_report
    def build(scan, **options):
        result = original(scan, **options)
        scan["pages"].clear()
        if options["recovery"] is not None:
            options["recovery"]["pages"].clear()
        return result
    monkeypatch.setattr(policy, "build_scan_omission_report", build)
    result = run(files, recovery_path=files.saved)
    assert len(result["scan"]["pages"]) == 1
    assert result["diagnostics"]["summary"]["unavailable_pages"] == 1


def test_native_import_version_difference_does_not_change_request_identity(files, monkeypatch):
    expected = request(files)
    def unloaded():
        value = native_metadata(digest(files.repo / io._LOCKS[0]))
        for package in value["packages"]:
            package.update(loaded_versions=[], loaded_version_agrees=None)
        return value
    monkeypatch.setattr(io, "_runtime_metadata", unloaded)
    other = request(files)
    assert expected.request_json == other.request_json


def test_model_free_source_scope_is_fixed_and_contains_no_model_policy_paths():
    assert {"ocr_scan_io.py", "ocr_scan_runtime.py", "ocr_scan_omission.py", "tools/inspect_ocr_scan.py"} <= set(io._PRODUCERS)
    assert len(io._PRODUCERS) == len(set(io._PRODUCERS))
    assert not any("model_artifacts" in name or "rapidocr" in name for name in io._PRODUCERS)


def test_busy_bundle_lease_refuses_without_native_execution(files):
    ready, release = threading.Event(), threading.Event()
    def hold():
        with PathLease(files.output, backend="ocr-scan", collection_name="bundle", operation="test", timeout=0):
            ready.set()
            release.wait(15)
    owner = threading.Thread(target=hold)
    owner.start()
    assert ready.wait(5)
    try:
        with pytest.raises(PathLeaseBusyError):
            run(files)
    finally:
        release.set()
        owner.join(5)
    assert not owner.is_alive() and not files.output.exists() and not files.seen


def historical(files, **kwargs):
    return io.read_scan_review_bundle(files.output, source_sha256=digest(files.source), page_count=1,
        recovery=json.loads(files.saved.read_bytes()), recovery_sha256=digest(files.saved), **kwargs)


def test_source_only_history_preserves_original_and_separately_rebuilds_current_review(files):
    def available(value):
        page = value["pages"][0]
        page.update(geometry={"width_points": 24., "height_points": 24., "rotation": 0, "cropbox": [0., 0., 24., 24.]},
                    raster={"width": 100, "height": 100, "dpi": 300, "format": "gray8", "pixel_sha256": "c" * 64},
                    discovery={"status": "available", "reason": "bounded_pixel_discovery", "threshold": 100.,
                        "threshold_foreground_pixels": 15, "foreground_accounting_complete": True,
                        "regions": [{"region_id": "scan-00001", "kind": "text_like",
                            "reason": "aligned_glyph_scale_components_not_text_proof", "bbox": [10, 60, 50, 65],
                            "component_count": 3, "foreground_pixels": 15}],
                        "work": {"rule_horizontal_runs": 0, "residual_horizontal_runs": 3,
                                 "rule_component_count": 0, "residual_component_count": 3, "pair_tests": 3}})
    files.mutate = available
    created = run(files)
    before = {name: (files.output / name).read_bytes() for name in io._LIMITS}
    value = historical(files)
    assert value["diagnostics"] == created["diagnostics"]
    assert value["diagnostics"]["recovery_sha256"] is None
    assert value["diagnostics"]["summary"]["ocr_unevaluated_regions"] == 1
    assert value["review_diagnostics"]["recovery_sha256"] == digest(files.saved)
    assert value["review_diagnostics"]["summary"]["potential_ocr_omissions"] == 1
    assert value["review_diagnostics"]["accuracy_verified"] is False
    assert all((files.output / name).read_bytes() == raw for name, raw in before.items())


def test_historical_import_accepts_changed_current_producers_locks_and_environment_only_as_history(files, monkeypatch):
    expected = request(files, installation_path=files.installation)
    created = run(files, installation_path=files.installation)
    (files.repo / "ocr_scan_runtime.py").write_bytes(b"new producer generation")
    (files.repo / "requirements-full.lock").write_bytes(b"new unrelated lock")
    files.installation.write_bytes(b"old installation no longer available")
    def prohibited(*_a, **_k):
        pytest.fail("historical review must not inspect current runtime or old external installation")
    monkeypatch.setattr(io, "_generation", prohibited)
    monkeypatch.setattr(io, "_runtime_metadata", prohibited)
    monkeypatch.setattr(io, "validate_installation_evidence", prohibited)
    result = historical(files)
    assert result["manifest"] == created["manifest"]
    assert result["manifest"]["installation_evidence"] == "verified_local_bindings"
    with pytest.raises(RuntimeError):
        io.read_scan_completion(files.output, request=expected)


def test_bound_historical_diagnostics_require_exact_original_saved_inputs(files):
    run(files, recovery_path=files.saved, proposals_path=files.proposals)
    proposed = json.loads(files.proposals.read_bytes())
    result = historical(files, proposals=proposed, proposals_sha256=digest(files.proposals))
    assert result["diagnostics"] == result["review_diagnostics"]
    with pytest.raises(ValueError):
        historical(files)
    with pytest.raises(ValueError):
        historical(files, proposals=proposed, proposals_sha256="f" * 64)
    files.saved.write_bytes(files.saved.read_bytes() + b" ")
    with pytest.raises(ValueError):
        historical(files, proposals=proposed, proposals_sha256=digest(files.proposals))


@pytest.mark.parametrize("mutate", [lambda m: m.update(schema_version=True),
    lambda m: m.update(scope="certified"), lambda m: m["request"].update(source_sha256="f" * 64),
    lambda m: m["request"].update(requested_pages=[True]),
    lambda m: m["request"]["generation"].update(extra="PRIVATE"),
    lambda m: m["request"]["generation"]["producer_sources"].update({"unexpected.py": "f" * 64}),
    lambda m: m["request"]["generation"]["producer_sources"].pop("ocr_scan_runtime.py"),
    lambda m: m["request"]["generation"]["executables"].update(python="not-a-hash"),
    lambda m: m["request"]["locks"].update({"requirements-full.lock": "f" * 64}),
    lambda m: m["request"].update(proposals_sha256="e" * 64),
    lambda m: m.update(installation_evidence="verified_local_bindings"),
    lambda m: m["runtime"].update(elapsed_seconds=True),
    lambda m: m["runtime"]["packages"].update(locked_environment_verified=True),
    lambda m: m["runtime"]["packages"]["packages"][0].update(loaded_versions=[], loaded_version_agrees=None),
    lambda m: m["runtime"]["packages"]["packages"][0].update(loaded_versions=["9.9"], loaded_version_agrees=True),
    lambda m: m["runtime"]["packages"]["packages"][0].update(loaded_version_agrees=1),
    lambda m: m["request"]["generation"]["runtime"]["environment"].update(python_version="3.14"),
    lambda m: m["runtime"]["packages"]["environment"].update(platform_machine="PRIVATE path / user")])
def test_historical_declaration_forgeries_rejected_even_after_rebinding_request_hash(files, mutate):
    run(files)
    def change(value):
        mutate(value)
        value["request_sha256"] = io._digest(value["request"])
    rebound(files, "manifest.json", change)
    with pytest.raises(ValueError):
        historical(files)


@pytest.mark.parametrize("name", ["scan.json", "diagnostics.json", "manifest.json"])
def test_historical_import_rechecks_every_fixed_file_after_validation(files, monkeypatch, name):
    run(files)
    original = policy.build_scan_omission_report
    calls = 0
    def mutate(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            path = files.output / name
            path.write_bytes(path.read_bytes() + b" ")
        return result
    monkeypatch.setattr(policy, "build_scan_omission_report", mutate)
    with pytest.raises(RuntimeError):
        historical(files)


@pytest.mark.parametrize("value", [True, 0, 5001, 1.0])
def test_historical_review_expected_page_count_is_strict(files, value):
    run(files)
    with pytest.raises(ValueError):
        io.read_scan_review_bundle(files.output, source_sha256=digest(files.source), page_count=value,
            recovery=json.loads(files.saved.read_bytes()), recovery_sha256=digest(files.saved))


def test_historical_bundle_source_and_input_objects_are_bound_and_detached(files):
    run(files)
    saved = json.loads(files.saved.read_bytes())
    before = copy.deepcopy(saved)
    with pytest.raises(ValueError):
        io.read_scan_review_bundle(files.output, source_sha256="f" * 64, page_count=1,
            recovery=saved, recovery_sha256=digest(files.saved))
    value = io.read_scan_review_bundle(files.output, source_sha256=digest(files.source), page_count=1,
            recovery=saved, recovery_sha256=digest(files.saved))
    value["review_diagnostics"]["pages"].clear()
    assert saved == before


def test_historical_no_native_import_or_execution(files, monkeypatch):
    run(files)
    monkeypatch.delitem(sys.modules, "ocr_scan_runtime")
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"ocr_scan_runtime", "pymupdf", "cv2", "numpy", "rapidocr", "onnxruntime", "model_artifacts"}:
            pytest.fail("historical import attempted native or model loading")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    historical(files)


def test_historical_hardlinked_or_rebound_tampered_artifacts_are_rejected(files, tmp_path):
    run(files)
    alias = tmp_path / "historical-alias"
    os.link(files.output / "scan.json", alias)
    with pytest.raises(ValueError):
        historical(files)
    alias.unlink()
    rebound(files, "diagnostics.json", lambda value: value["summary"].update(unavailable_pages=0))
    with pytest.raises(ValueError):
        historical(files)


@pytest.mark.parametrize("api", [io.scan_request, io.run_scan_bundle])
@pytest.mark.parametrize("recipe", [None, True, 1, [], {}, "", "legacy", "spatial-v1", "spatial-v2 ", PRIVATE])
def test_unknown_recipe_rejected_before_files_runtime_or_native(files, monkeypatch, api, recipe):
    def prohibited(*_args, **_kwargs):
        pytest.fail("unknown recipe must fail before input/runtime inspection")
    monkeypatch.setattr(io, "_snapshot", prohibited)
    monkeypatch.setattr(io, "_generation", prohibited)
    with pytest.raises(ValueError):
        api(files.source, files.output, requested_pages=[1], recipe=recipe)
    assert not files.seen and not files.output.exists()


def test_explicit_legacy_recipe_preserves_request_identity_and_native_call_shape(files, monkeypatch):
    default = request(files)
    explicit = request(files, recipe="legacy-v1")
    assert default.recipe == explicit.recipe == "legacy-v1"
    assert default.request_json == explicit.request_json
    assert "recipe" not in json.loads(explicit.request_json)
    assert json.loads(explicit.request_json)["configuration"] == policy.DEFAULT_CONFIGURATION
    with pytest.raises(FrozenInstanceError):
        explicit.recipe = "spatial-v2"
    seen = []
    def legacy_collect(raw, *, requested_pages):
        seen.append((raw, requested_pages))
        return observation(hashlib.sha256(raw).hexdigest(), requested_pages)
    monkeypatch.setattr(sys.modules["ocr_scan_runtime"], "collect_scan_observation", legacy_collect)
    bundle = run(files, recipe="legacy-v1")
    assert seen == [(files.source.read_bytes(), [1])]
    assert bundle["scan"]["schema_version"] == bundle["diagnostics"]["schema_version"] == bundle["manifest"]["schema_version"] == 1
    assert io.read_scan_completion(files.output, request=default) == bundle
    assert historical(files)["scan"] == bundle["scan"]


@pytest.mark.parametrize("mode", ["source_only", "recovery", "proposals", "installation"])
def test_spatial_recipe_survives_native_private_publication_and_fresh_readback(files, mode):
    options = {"recipe": "spatial-v2"}
    if mode in {"recovery", "proposals"}:
        options["recovery_path"] = files.saved
    if mode == "proposals":
        options["proposals_path"] = files.proposals
    if mode == "installation":
        options["installation_path"] = files.installation
    expected = request(files, **options)
    created = run(files, **options)
    assert expected.recipe == "spatial-v2" and files.recipes == ["spatial-v2"]
    assert created["scan"]["schema_version"] == 2
    assert created["diagnostics"]["schema_version"] == created["manifest"]["schema_version"] == 1
    assert created["manifest"]["request"]["configuration"] == policy.configuration_for_recipe("spatial-v2")
    assert "recipe" not in created["manifest"]["request"]
    assert set(created["manifest"]["request"]["generation"]["producer_sources"]) == io._HISTORICAL_PRODUCERS_V1
    assert io.read_scan_completion(files.output, request=expected) == created
    assert io.read_scan_completion(files.output, request=request(files, readback=True, **options)) == created
    history_options = ({"proposals": json.loads(files.proposals.read_bytes()), "proposals_sha256": digest(files.proposals)}
                       if mode == "proposals" else {})
    reviewed = historical(files, **history_options)
    assert reviewed["scan"] == created["scan"] and reviewed["diagnostics"] == created["diagnostics"]
    assert reviewed["review_diagnostics"]["schema_version"] == 1


@pytest.mark.parametrize("recipe,other", [("legacy-v1", "spatial-v2"), ("spatial-v2", "legacy-v1")])
@pytest.mark.parametrize("changed", ["selection", "configuration"])
def test_frozen_recipe_selection_and_request_configuration_cannot_diverge(files, recipe, other, changed):
    expected = request(files, recipe=recipe)
    if changed == "selection":
        bad = replace(expected, recipe=other)
    else:
        facts = json.loads(expected.request_json)
        facts["configuration"] = policy.configuration_for_recipe(other)
        bad = replace(expected, request_json=io._canonical(facts))
    with pytest.raises(RuntimeError):
        io.recheck_scan_request(bad)
    assert not files.seen and not files.output.exists()


@pytest.mark.parametrize("recipe,other", [("legacy-v1", "spatial-v2"), ("spatial-v2", "legacy-v1")])
def test_parent_refuses_other_valid_worker_recipe_even_when_all_artifacts_are_consistent(files, recipe, other):
    expected = request(files, recipe=recipe)
    result = run(files, recipe=other)
    assert policy.recipe_for_configuration(result["scan"]["configuration"]) == other
    with pytest.raises(ValueError):
        io.read_scan_completion(files.output, request=expected)
    assert historical(files)["scan"] == result["scan"]


@pytest.mark.parametrize("recipe,other", [("legacy-v1", "spatial-v2"), ("spatial-v2", "legacy-v1")])
def test_other_valid_native_recipe_is_rejected_before_any_artifact_publication(files, monkeypatch, recipe, other):
    def wrong_recipe(raw, *, requested_pages, **_options):
        return observation(hashlib.sha256(raw).hexdigest(), requested_pages, recipe=other)
    monkeypatch.setattr(sys.modules["ocr_scan_runtime"], "collect_scan_observation", wrong_recipe)
    with pytest.raises(ValueError):
        run(files, recipe=recipe)
    assert files.output.is_dir() and not any((files.output / name).exists() for name in io._LIMITS)


def test_scan_validator_rechecks_frozen_selection_even_if_request_json_is_rebound(files):
    expected = request(files)
    facts = json.loads(expected.request_json)
    facts["configuration"] = policy.configuration_for_recipe("spatial-v2")
    forged = replace(expected, request_json=io._canonical(facts))
    value = observation(digest(files.source), [1], recipe="spatial-v2")
    policy.validate_scan_observation(value)
    with pytest.raises(ValueError):
        io._validate_scan(value, forged)


@pytest.mark.parametrize("mutate", [lambda value: value.update(schema_version=1),
    lambda value: value["configuration"].update(algorithm=policy.DEFAULT_CONFIGURATION["algorithm"]),
    lambda value: value["configuration"].update(spatial_cell_pixels=128.0),
    lambda value: value["pages"][0]["discovery"]["work"].pop("spatial_bucket_visits"),
    lambda value: value["pages"][0]["discovery"]["work"].update(spatial_index_entries=True)])
@pytest.mark.parametrize("reader", ["fresh", "historical"])
def test_rebound_hybrid_or_type_coerced_recipe_never_imports(files, mutate, reader):
    expected = request(files, recipe="spatial-v2")
    run(files, recipe="spatial-v2")
    rebound(files, "scan.json", mutate)
    def align_manifest(value):
        value["request"]["configuration"] = json.loads((files.output / "scan.json").read_bytes())["configuration"]
        value["request_sha256"] = io._digest(value["request"])
    rebound(files, "manifest.json", align_manifest)
    with pytest.raises(ValueError):
        io.read_scan_completion(files.output, request=expected) if reader == "fresh" else historical(files)


def test_spatial_historical_reader_accepts_old_generation_without_native_or_metadata_imports(files, monkeypatch):
    expected = request(files, recipe="spatial-v2", installation_path=files.installation)
    created = run(files, recipe="spatial-v2", installation_path=files.installation)
    (files.repo / "ocr_scan_runtime.py").write_bytes(b"new current producer")
    (files.repo / "requirements-full.lock").write_bytes(b"new current lock")
    files.installation.write_bytes(b"old external installation evidence unavailable")
    monkeypatch.delitem(sys.modules, "ocr_scan_runtime")
    def prohibited(*_args, **_kwargs):
        pytest.fail("historical review must not inspect current generations or load native libraries")
    monkeypatch.setattr(io, "_generation", prohibited)
    monkeypatch.setattr(io, "_runtime_metadata", prohibited)
    monkeypatch.setattr(io, "validate_installation_evidence", prohibited)
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"ocr_scan_runtime", "pymupdf", "cv2", "numpy", "rapidocr", "onnxruntime", "model_artifacts"}:
            prohibited()
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    reviewed = historical(files)
    assert reviewed["scan"] == created["scan"] and reviewed["manifest"] == created["manifest"]
    with pytest.raises(RuntimeError):
        io.read_scan_completion(files.output, request=expected)


@pytest.mark.parametrize("recipe", ["legacy-v1", "spatial-v2"])
def test_review_adapter_accepts_both_recipe_histories_without_rendering_unavailable_pixels(files, monkeypatch, recipe):
    from ocr_scan_review import ScanReview

    run(files, recipe=recipe)
    monkeypatch.delitem(sys.modules, "ocr_scan_runtime")
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"ocr_scan_runtime", "pymupdf", "cv2", "numpy", "PIL", "rapidocr", "model_artifacts"}:
            pytest.fail("unavailable historical pixels must not cause renderer/model imports")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    before = {name: (files.output / name).read_bytes() for name in io._LIMITS}
    adapter = ScanReview(files.output, files.source, source_sha256=digest(files.source), page_count=1,
                        recovery=json.loads(files.saved.read_bytes()), recovery_sha256=digest(files.saved))
    assert adapter.page(1)["scan_status"] == "unavailable"
    assert adapter.render(1) is None
    assert all((files.output / name).read_bytes() == raw for name, raw in before.items())


def test_recipe_string_subclass_is_not_coerced_to_legacy_before_admission(files):
    class Recipe(str):
        pass
    with pytest.raises(ValueError):
        run(files, recipe=Recipe("legacy-v1"))
    assert not files.output.exists() and not files.seen
