"""Synthetic JSON/source-byte tests; no PDF parsing or OCR/model execution."""

import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import ocr_context_evaluation_io as io
import ocr_recovery
import storage_policy


TEXT = "Synthetic Alice owes 10 dollars."


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


@pytest.fixture
def inputs(tmp_path):
    paths = {key: tmp_path / (key + suffix) for key, suffix in
             (("pdf", ".pdf"), ("recovery", ".json"), ("reference", ".json"),
              ("correspondence", ".json"), ("output", ".json"))}
    paths["pdf"].write_bytes(b"%PDF-SYNTHETIC-BYTES-NOT-A-PARSED-DOCUMENT")

    class Reader:
        page_count = 1

        def native_text(self, number):
            return ""

        def retry(self, number):
            return {"text": TEXT, "lines": [{"text": TEXT, "score": .9,
                                            "box": [[0, 0], [80, 0], [80, 20], [0, 20]]}],
                    "mean_confidence": .9,
                    "raster": {"width": 100, "height": 100, "dpi": 300, "coordinate_system": "rendered_image_pixels"},
                    "engine": {"name": "rapidocr", "version": "synthetic", "min_score": 0., "max_side": 6000}}

    recovery = ocr_recovery.build_recovery_report(
        Reader(), source_sha256=digest(paths["pdf"]), policy=ocr_recovery.RetryPolicy())
    recovery["evidence_sha256"] = None
    dump(paths["recovery"], recovery)
    start = TEXT.index("10")
    ref = {"schema_version": 1, "kind": "ocr_context_reference", "source_sha256": digest(paths["pdf"]),
           "page_count": 1, "approval": "human_reviewed", "coordinate_system": "original_page_display_fraction",
           "contexts": [{"context_id": "synthetic_ctx", "page_number": 1,
                         "source_anchor": {"kind": "sentence", "bbox": [.1, .1, .9, .2], "cell": None},
                         "reference": TEXT, "checks": [{"check_id": "numeric", "category": "number",
                            "reference_span": [start, start+2], "left_anchor": TEXT[:start], "right_anchor": TEXT[start+2:]}]}]}
    dump(paths["reference"], ref)
    mapping = {"schema_version": 1, "kind": "ocr_context_correspondence", "source_sha256": digest(paths["pdf"]),
               "recovery_sha256": digest(paths["recovery"]), "reference_sha256": digest(paths["reference"]),
               "approval": "human_reviewed", "offset_unit": "raw_unicode_code_points",
               "contexts": [{"context_id": "synthetic_ctx", "status": "mapped", "candidate_span": [0, len(TEXT)]}]}
    dump(paths["correspondence"], mapping)
    return paths


def run(paths):
    return io.evaluate_context_files(*(paths[key] for key in ("pdf", "recovery", "reference", "correspondence", "output")))


def test_complete_private_publication_binds_exact_bytes_and_never_repeats_input_text(inputs):
    before = {key: path.read_bytes() for key, path in inputs.items() if key != "output"}
    report = run(inputs)
    assert report["coverage"]["checks"]["passed"] == 1
    assert report["inputs"] == {key + "_sha256": digest(inputs[key]) for key in
                                ("pdf", "recovery", "reference", "correspondence")}
    assert json.loads(inputs["output"].read_bytes()) == report
    serialized = inputs["output"].read_text(encoding="utf-8")
    assert "Alice" not in serialized and "synthetic_ctx" not in serialized
    assert all(path.read_bytes() == before[key] for key, path in inputs.items() if key != "output")
    if os.name == "nt":
        assert storage_policy.windows_path_is_private(inputs["output"], directory=False)
    else:
        assert stat.S_IMODE(inputs["output"].stat().st_mode) == 0o600


@pytest.mark.parametrize("first,second", list(itertools.combinations(("pdf", "recovery", "reference", "correspondence", "output"), 2)))
def test_identical_paths_are_rejected_before_reads(inputs, first, second, monkeypatch):
    inputs[second] = inputs[first]
    monkeypatch.setattr(io, "_read_snapshot", lambda *_a, **_k: pytest.fail("read before alias validation"))
    with pytest.raises(ValueError):
        run(inputs)


def test_hardlink_alias_and_symlink_are_rejected(inputs, tmp_path):
    alias = tmp_path / "alias.json"
    try:
        os.link(inputs["reference"], alias)
    except OSError:
        pytest.skip("hardlinks unavailable")
    changed = dict(inputs, correspondence=alias)
    with pytest.raises(ValueError):
        run(changed)
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(inputs["reference"])
    except OSError:
        return
    with pytest.raises((ValueError, OSError)):
        run(dict(inputs, reference=link))


def test_existing_output_is_preserved_before_loading(inputs, monkeypatch):
    inputs["output"].write_bytes(b"OWNER-DATA")
    monkeypatch.setattr(io, "_read_snapshot", lambda *_a, **_k: pytest.fail("read before no-clobber"))
    with pytest.raises(FileExistsError):
        run(inputs)
    assert inputs["output"].read_bytes() == b"OWNER-DATA"


def test_noncooperating_output_creation_is_not_overwritten(inputs, monkeypatch):
    publish = ocr_recovery._publish_new_report

    def race(temporary, destination):
        destination.write_bytes(b"RACER")
        publish(temporary, destination)

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", race)
    with pytest.raises(FileExistsError):
        run(inputs)
    assert inputs["output"].read_bytes() == b"RACER"


@pytest.mark.parametrize("key", ["pdf", "recovery", "reference", "correspondence"])
@pytest.mark.parametrize("stage", ["evaluation", "writer"])
def test_each_input_rechecked_inside_actual_commit_after_serialization(inputs, monkeypatch, key, stage):
    if stage == "evaluation":
        original = io.evaluate_context_checks

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            inputs[key].write_bytes(inputs[key].read_bytes() + b" ")
            return result

        monkeypatch.setattr(io, "evaluate_context_checks", changed)
    else:
        original = storage_policy.atomic_write_private_json

        def changed(path, payload, **kwargs):
            publish = kwargs["replace_fn"]

            def commit(temporary, destination):
                assert temporary.exists()
                inputs[key].write_bytes(inputs[key].read_bytes() + b" ")
                publish(temporary, destination)

            kwargs["replace_fn"] = commit
            return original(path, payload, **kwargs)

        monkeypatch.setattr(storage_policy, "atomic_write_private_json", changed)
    with pytest.raises((ValueError, RuntimeError)):
        run(inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("key", ["pdf", "recovery", "reference", "correspondence"])
def test_initial_source_and_exact_json_generation_bindings(inputs, key):
    inputs[key].write_bytes(inputs[key].read_bytes() + b" ")
    if key == "correspondence":
        # Correspondence is the current reviewed input, not self-hashed.
        assert run(inputs)["inputs"]["correspondence_sha256"] == digest(inputs[key])
    else:
        with pytest.raises(ValueError):
            run(inputs)
        assert not inputs["output"].exists()


@pytest.mark.parametrize("key,budget", [("pdf", "MAX_PDF_BYTES"), ("recovery", "MAX_RECOVERY_BYTES"),
                                       ("reference", "MAX_REFERENCE_BYTES"), ("correspondence", "MAX_CORRESPONDENCE_BYTES")])
def test_input_byte_limits_are_enforced(inputs, monkeypatch, key, budget):
    monkeypatch.setattr(io, budget, inputs[key].stat().st_size - 1)
    with pytest.raises(ValueError):
        run(inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":1}', b'{"bad":NaN}', b'{"bad":1e999}',
                                 b'[]', b'{"bad":"\xff"}', b'{"bad":' + b'['*65 + b'0'+b']'*65+b'}'])
def test_strict_json_rejection_before_publication(inputs, raw):
    inputs["correspondence"].write_bytes(raw)
    with pytest.raises(ValueError):
        run(inputs)
    assert not inputs["output"].exists()


def test_lease_covers_all_snapshots_hashing_and_commit(inputs, monkeypatch):
    active, events = [], []
    read, hashed, publish = io._read_snapshot, io._source_digest, ocr_recovery._publish_new_report

    class Lease:
        def __init__(self, path, **kwargs):
            assert path == inputs["output"].absolute()
            assert kwargs == {"backend": "ocr-context", "collection_name": "report", "operation": "evaluate",
                              "timeout": 0, "resource_description": "OCR context evaluation report"}

        def __enter__(self):
            active.append(True)

        def __exit__(self, *_args):
            active.pop()

    def reading(*args, **kwargs):
        assert active
        events.append("read")
        return read(*args, **kwargs)

    def hashing(*args, **kwargs):
        assert active
        events.append("hash")
        return hashed(*args, **kwargs)

    def publishing(*args):
        assert active
        events.append("publish")
        return publish(*args)

    monkeypatch.setattr(io, "PathLease", Lease)
    monkeypatch.setattr(io, "_read_snapshot", reading)
    monkeypatch.setattr(io, "_source_digest", hashing)
    monkeypatch.setattr(ocr_recovery, "_publish_new_report", publishing)
    run(inputs)
    assert events == ["read"]*3 + ["hash"]*2 + ["read"]*3 + ["publish"]


@pytest.mark.parametrize("failure", [KeyboardInterrupt, ocr_recovery.ReportCleanupError])
def test_late_outcomes_preserve_completed_report(inputs, monkeypatch, failure):
    actual = ocr_recovery._publish_new_report

    def publish(temporary, destination):
        actual(temporary, destination)
        raise failure("SYNTHETIC_PRIVATE")

    monkeypatch.setattr(ocr_recovery, "_publish_new_report", publish)
    with pytest.raises(failure):
        run(inputs)
    assert json.loads(inputs["output"].read_bytes())["coverage"]["checks"]["passed"] == 1


def test_source_directory_or_empty_source_is_rejected(inputs, tmp_path):
    with pytest.raises(ValueError):
        io._source_digest(tmp_path)
    inputs["pdf"].write_bytes(b"")
    with pytest.raises(ValueError):
        io._source_digest(inputs["pdf"])


def test_actual_cli_synthetic_artifacts_no_ocr_or_pdf_parser(inputs):
    args = [sys.executable, "tools/evaluate_ocr_context.py"]
    for key in ("pdf", "recovery", "reference", "correspondence", "output"):
        args.extend(["--"+key, str(inputs[key])])
    result = subprocess.run(args, cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert "1 passed, 0 failed, 0 abstained" in result.stdout.decode()
    assert "Alice" not in result.stdout.decode()+result.stderr.decode()


def test_strict_recovery_validation_not_trusting_forged_summary(inputs):
    recovery = json.loads(inputs["recovery"].read_bytes())
    recovery["summary"]["selected"] = 0
    dump(inputs["recovery"], recovery)
    mapping = json.loads(inputs["correspondence"].read_bytes())
    mapping["recovery_sha256"] = digest(inputs["recovery"])
    dump(inputs["correspondence"], mapping)
    with pytest.raises(ValueError):
        run(inputs)
    assert not inputs["output"].exists()


def test_source_hash_read_budget_survives_growth_after_preflight(inputs, monkeypatch):
    original_open = Path.open
    limit = inputs["pdf"].stat().st_size
    monkeypatch.setattr(io, "MAX_PDF_BYTES", limit)

    class GrowingHandle:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def fileno(self):
            return self.handle.fileno()

        def seek(self, offset):
            return self.handle.seek(offset)

        def read(self, count):
            assert count <= limit + 1
            with original_open(inputs["pdf"], "ab") as writer:
                writer.write(b"x"*10)
            return self.handle.read(count)

    def opening(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return GrowingHandle(handle) if path == inputs["pdf"] and args == ("rb",) else handle

    monkeypatch.setattr(Path, "open", opening)
    with pytest.raises(ValueError, match="streaming"):
        io._source_digest(inputs["pdf"])


def test_reference_and_mapping_payloads_are_never_mutated(inputs, monkeypatch):
    actual = io.evaluate_context_checks

    def evaluate(reference, correspondence, **kwargs):
        before = copy.deepcopy((reference, correspondence, kwargs))
        result = actual(reference, correspondence, **kwargs)
        assert (reference, correspondence, kwargs) == before
        return result

    monkeypatch.setattr(io, "evaluate_context_checks", evaluate)
    run(inputs)
