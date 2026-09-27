"""Portable private crop-review declarations, separate from live-run authority.

This pure contract retains original verification bytes, not PDFs, model files,
browser sessions or execution tickets. A valid archive is self-consistent local
evidence, not authenticated execution or human approval. The host must separately
verify its fixed source and render a fresh crop before allowing a new review.
"""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

from evaluation_inputs import _strict_json_bytes
import ocr_crop_comparison as crops


MAX_PACK_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_REVIEW_BYTES = 3 * 1024 * 1024
VALIDATION_SCOPE = "historical_local_declarations"
ARTIFACT_LIMITS = MappingProxyType({
    "manifest.json": 512 * 1024, "report.json": 128 * 1024 * 1024,
    "execution.json": 4 * 1024 * 1024, "disposition.json": 16 * 1024 * 1024,
    "work/report.json": 128 * 1024 * 1024,
})
INPUT_LIMITS = MappingProxyType({
    "recovery.json": 64 * 1024 * 1024, "plan.json": 1024 * 1024,
    "requirements-full.lock": 8 * 1024 * 1024,
    "requirements-test.lock": 8 * 1024 * 1024,
    "requirements-lock-tools.lock": 8 * 1024 * 1024,
    "model-artifact-policy.json": 8 * 1024 * 1024,
    "model-artifacts.lock.json": 8 * 1024 * 1024,
    "installation.json": 64 * 1024,
})
SLOT_LIMITS = MappingProxyType({
    **{f"{side}/artifacts/{name}": limit for side in ("baseline", "retry")
       for name, limit in ARTIFACT_LIMITS.items()},
    **{f"{side}/inputs/{name}": limit for side in ("baseline", "retry")
       for name, limit in INPUT_LIMITS.items()},
    "review.json": MAX_REVIEW_BYTES,
})
_OPTIONAL = frozenset(f"{side}/inputs/installation.json" for side in ("baseline", "retry"))
_REQUIRED = frozenset(SLOT_LIMITS) - _OPTIONAL
_MANIFEST_KEYS = {"schema_version", "kind", "source_sha256", "recovery_sha256", "page_count",
    "scope", "pair", "review_state", "files", "total_file_bytes", "parent_pack_sha256",
    "validation_scope", "requires_attention", "canonical_extraction_modified"}


def _fail():
    raise ValueError("invalid crop review pack or binding")


def _fields(value, keys):
    if (type(value) is not dict or len(value) != len(keys)
            or any(type(key) is not str for key in value) or set(value) != set(keys)):
        _fail()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode_manifest(payload):
    """Canonical bytes used for the pack identity; no trailing newline."""
    return crops._bytes(payload, MAX_MANIFEST_BYTES)


def _decode(raw, maximum):
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        _fail()
    try:
        payload = _strict_json_bytes(raw, label="crop review pack", max_bytes=maximum)
        crops._bytes(payload, maximum)
        return payload
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        _fail()


def validate_crop_pack_manifest(payload, *, source_sha256, recovery_sha256, page_count):
    """Validate declarations only; neither file presence nor consent is proved."""
    encode_manifest(payload)
    _fields(payload, _MANIFEST_KEYS)
    crops._hex(source_sha256)
    crops._hex(recovery_sha256)
    if (type(page_count) is not int or not 1 <= page_count <= 5000
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or payload["kind"] != "ocr_crop_review_pack"
            or payload["source_sha256"] != source_sha256
            or payload["recovery_sha256"] != recovery_sha256
            or type(payload["page_count"]) is not int or payload["page_count"] != page_count
            or payload["validation_scope"] != VALIDATION_SCOPE
            or payload["requires_attention"] is not True
            or payload["canonical_extraction_modified"] is not False
            or payload["review_state"] not in ("draft", "historical_operator_declaration")):
        _fail()
    crops.validate_crop_scope(payload["scope"], source_sha256=source_sha256, page_count=page_count)
    if payload["parent_pack_sha256"] is not None:
        crops._hex(payload["parent_pack_sha256"])
    _fields(payload["pair"], {"baseline", "retry"})
    for pin in payload["pair"].values():
        _fields(pin, {"region_id", "report_sha256", "request_sha256"})
        crops._id(pin["region_id"])
        crops._hex(pin["report_sha256"])
        crops._hex(pin["request_sha256"])
    descriptors = payload["files"]
    if (type(descriptors) is not dict or not len(_REQUIRED) <= len(descriptors) <= len(SLOT_LIMITS)
            or any(type(name) is not str for name in descriptors) or not _REQUIRED <= set(descriptors)
            or not set(descriptors) <= set(SLOT_LIMITS)):
        _fail()
    total = 0
    for name, descriptor in descriptors.items():
        _fields(descriptor, {"sha256", "bytes"})
        crops._hex(descriptor["sha256"])
        size = descriptor["bytes"]
        if type(size) is not int or not 1 <= size <= SLOT_LIMITS[name]:
            _fail()
        total += size
    if (type(payload["total_file_bytes"]) is not int or payload["total_file_bytes"] != total
            or total > MAX_PACK_BYTES - MAX_MANIFEST_BYTES):
        _fail()
    for side in ("baseline", "retry"):
        if (descriptors[f"{side}/artifacts/report.json"]["sha256"] != payload["pair"][side]["report_sha256"]
                or descriptors[f"{side}/inputs/recovery.json"]["sha256"] != recovery_sha256):
            _fail()
    return json.loads(encode_manifest(payload))


def _draft(value):
    _fields(value, {"reference", "critical_tokens_text"})
    for key, maximum in (("reference", 20_000), ("critical_tokens_text", 64 * 257)):
        if type(value[key]) is not str or len(value[key]) > maximum:
            _fail()
    # Partial/empty critical entries are valid drafts. Do not trim, normalize,
    # silently split, or substitute an older reviewed transcription.
    return json.loads(crops._bytes(value, crops.MAX_REFERENCE_BYTES))


def _review(raw, manifest):
    descriptor = manifest["files"]["review.json"]
    if type(raw) is not bytes or len(raw) != descriptor["bytes"] or _sha(raw) != descriptor["sha256"]:
        _fail()
    value = _decode(raw, MAX_REVIEW_BYTES)
    _fields(value, {"schema_version", "kind", "draft", "reviewed"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "ocr_crop_review_state"):
        _fail()
    _draft(value["draft"])
    if value["reviewed"] is None:
        if manifest["review_state"] != "draft":
            _fail()
    else:
        _fields(value["reviewed"], {"reference", "comparison", "source_image"})
        if manifest["review_state"] != "historical_operator_declaration":
            _fail()
        image = value["reviewed"]["source_image"]
        _fields(image, {"width", "height", "rgb_sha256"})
        if any(type(image[key]) is not int or not 1 <= image[key] <= 1400 for key in ("width", "height")):
            _fail()
        crops._hex(image["rgb_sha256"])
        # These remain unverified declarations until complete archive replay.
        crops._bytes(value["reviewed"]["reference"], crops.MAX_REFERENCE_BYTES)
        crops._bytes(value["reviewed"]["comparison"], crops.MAX_RESULT_BYTES)
    return value


def recover_crop_pack_draft(manifest, review_raw, *, source_sha256, recovery_sha256, page_count):
    """Recover bounded authored text even when other evidence is unavailable.

    Deliberately returns NO OCR candidates, metrics, reviewed reference, image or
    authority token. A historical 'reviewed' declaration cannot upgrade this
    recovery-only result. Source identity here is a declaration supplied by the
    fixed host, not an instruction to open a path found inside an archive.
    """
    manifest = validate_crop_pack_manifest(manifest, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, page_count=page_count)
    review = _review(review_raw, manifest)
    return {"draft": review["draft"], "scope": manifest["scope"],
            "pack_sha256": _sha(encode_manifest(manifest)), "status": "unverified_saved_draft",
            "requires_attention": True, "canonical_extraction_modified": False}


def _files(files):
    if (type(files) is not dict or not len(_REQUIRED) <= len(files) <= len(SLOT_LIMITS)
            or any(type(name) is not str for name in files)
            or not _REQUIRED <= set(files) or not set(files) <= set(SLOT_LIMITS)):
        _fail()
    total = 0
    for name, raw in files.items():
        if type(raw) is not bytes or not 1 <= len(raw) <= SLOT_LIMITS[name]:
            _fail()
        total += len(raw)
        if total > MAX_PACK_BYTES - MAX_MANIFEST_BYTES:
            _fail()
    return {name: {"sha256": _sha(raw), "bytes": len(raw)} for name, raw in files.items()}, total


def _archives(files, *, source_sha256, recovery_sha256, page_count):
    from ocr_disposition_archive import validate_disposition_archive

    result = {}
    for side in ("baseline", "retry"):
        prefix = f"{side}/"
        archive = validate_disposition_archive(
            artifacts={name: files[prefix + "artifacts/" + name] for name in ARTIFACT_LIMITS},
            retained_inputs={name: files[prefix + "inputs/" + name] for name in INPUT_LIMITS
                             if prefix + "inputs/" + name in files},
            source_sha256=source_sha256, page_count=page_count)
        if (archive["validation_scope"] != VALIDATION_SCOPE
                or archive["retained_input_sha256"]["recovery.json"] != recovery_sha256):
            _fail()
        result[side] = archive
    return result


def _pair(archives, baseline_region_id, retry_region_id):
    pins, scopes = {}, []
    for side, region_id in (("baseline", baseline_region_id), ("retry", retry_region_id)):
        archive = archives[side]
        scopes.append(crops.crop_scope(archive["report"], region_id=region_id))
        pins[side] = {"region_id": region_id, "report_sha256": archive["artifact_sha256"]["report.json"],
                      "request_sha256": archive["request_sha256"]}
    if scopes[0] != scopes[1]:
        _fail()
    return pins, scopes[0]


def _replay(review, archives, pair):
    saved = review["reviewed"]
    if saved is None:
        return
    draft = review["draft"]
    tokens = [] if draft["critical_tokens_text"] == "" else draft["critical_tokens_text"].split("\n")
    if len(tokens) > 64 or any(not item.strip() or len(item) > 256 or "\r" in item for item in tokens):
        _fail()
    reference = crops.validate_crop_reference(saved["reference"], anchor_report=archives["baseline"]["report"],
        anchor_report_sha256=pair["baseline"]["report_sha256"])
    if reference["reference"] != draft["reference"] or reference["critical_tokens"] != tokens:
        _fail()
    expected = crops.compare_crop_candidates(archives["baseline"]["report"], archives["retry"]["report"], reference,
        baseline_report_sha256=pair["baseline"]["report_sha256"], retry_report_sha256=pair["retry"]["report_sha256"],
        baseline_region_id=pair["baseline"]["region_id"], retry_region_id=pair["retry"]["region_id"])
    if crops._bytes(expected, crops.MAX_RESULT_BYTES) != crops._bytes(saved["comparison"], crops.MAX_RESULT_BYTES):
        _fail()


def validate_crop_review_pack(manifest, *, files, source_sha256, recovery_sha256, page_count):
    """Replay full historical evidence without touching the current OCR runtime."""
    manifest = validate_crop_pack_manifest(manifest, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, page_count=page_count)
    descriptors, total = _files(files)
    if descriptors != manifest["files"] or total != manifest["total_file_bytes"]:
        _fail()
    review = _review(files["review.json"], manifest)
    archives = _archives(files, source_sha256=source_sha256, recovery_sha256=recovery_sha256, page_count=page_count)
    pair, scope = _pair(archives, manifest["pair"]["baseline"]["region_id"], manifest["pair"]["retry"]["region_id"])
    if pair != manifest["pair"] or scope != manifest["scope"]:
        _fail()
    _replay(review, archives, pair)
    return {"manifest": manifest, "review": review, "baseline": archives["baseline"], "retry": archives["retry"],
            "pack_sha256": _sha(encode_manifest(manifest)), "validation_scope": VALIDATION_SCOPE,
            "requires_attention": True, "canonical_extraction_modified": False}


def build_crop_review_pack(*, baseline, retry, source_sha256, recovery_sha256, page_count,
                          baseline_region_id, retry_region_id, reference, critical_tokens_text,
                          reviewed=None, parent_pack_sha256=None):
    """Build a pack from two complete raw archives and an explicit draft/review.

    Each side has exactly ``artifacts`` and ``retained_inputs`` byte mappings.
    A reviewed snapshot must come from the host's current successful comparison,
    NOT client-supplied metrics or a restored checkbox. This pure validation
    cannot authenticate that host/operator history and never grants authority.
    """
    files = {}
    for side, value in (("baseline", baseline), ("retry", retry)):
        _fields(value, {"artifacts", "retained_inputs"})
        _fields(value["artifacts"], ARTIFACT_LIMITS)
        inputs = value["retained_inputs"]
        if (type(inputs) is not dict or not len(INPUT_LIMITS) - 1 <= len(inputs) <= len(INPUT_LIMITS)
                or any(type(name) is not str for name in inputs)
                or not set(INPUT_LIMITS) - {"installation.json"} <= set(inputs)
                or not set(inputs) <= set(INPUT_LIMITS)):
            _fail()
        for group, values in (("artifacts", value["artifacts"]), ("inputs", inputs)):
            files.update({f"{side}/{group}/{name}": raw for name, raw in values.items()})
    review = {"schema_version": 1, "kind": "ocr_crop_review_state",
              "draft": _draft({"reference": reference, "critical_tokens_text": critical_tokens_text}),
              "reviewed": reviewed}
    files["review.json"] = crops._bytes(review, MAX_REVIEW_BYTES)
    descriptors, total = _files(files)
    archives = _archives(files, source_sha256=source_sha256, recovery_sha256=recovery_sha256, page_count=page_count)
    pair, scope = _pair(archives, baseline_region_id, retry_region_id)
    manifest = {"schema_version": 1, "kind": "ocr_crop_review_pack", "source_sha256": source_sha256,
        "recovery_sha256": recovery_sha256, "page_count": page_count, "scope": scope, "pair": pair,
        "review_state": "draft" if reviewed is None else "historical_operator_declaration",
        "files": descriptors, "total_file_bytes": total, "parent_pack_sha256": parent_pack_sha256,
        "validation_scope": VALIDATION_SCOPE, "requires_attention": True, "canonical_extraction_modified": False}
    manifest = validate_crop_pack_manifest(manifest, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, page_count=page_count)
    _replay(_review(files["review.json"], manifest), archives, pair)
    return {"manifest": manifest, "files": files}
