"""Explicit v2 uncertainty packs, separate from v1 and live review authority.

Fixed archive slots and byte ceilings are inherited unchanged. Strict validation
joins real retained reports; recovery validates declaration-only journal replay
without inventing a report or calling a scorer. Neither path proves execution,
source authenticity, actual image display or current operator consent.
"""

from __future__ import annotations

import json

import ocr_crop_comparison as crops
import ocr_crop_raster_view as raster
import ocr_crop_review_pack as legacy
import ocr_crop_uncertainty_comparison as comparison
import ocr_crop_uncertainty_journal as history


MAX_PACK_BYTES = legacy.MAX_PACK_BYTES
MAX_MANIFEST_BYTES = legacy.MAX_MANIFEST_BYTES
MAX_REVIEW_BYTES = legacy.MAX_REVIEW_BYTES
VALIDATION_SCOPE = legacy.VALIDATION_SCOPE
_STATES = ("draft", "historical_scored_declaration", "historical_unscorable_declaration")
_EVIDENCE_KEYS = {"manifest", "review", "baseline", "retry", "pack_sha256", "validation_scope",
                  "requires_attention", "canonical_extraction_modified"}


def _fail():
    raise ValueError("invalid uncertainty crop pack or revision binding")


def _binding(manifest):
    return {key: manifest[key] for key in ("source_sha256", "recovery_sha256", "page_count")}


def validate_crop_pack_manifest_v2(payload, *, source_sha256, recovery_sha256, page_count):
    """Validate explicit v2 declarations with unchanged fixed slot semantics."""
    legacy._fields(payload, legacy._MANIFEST_KEYS)
    if (type(payload["schema_version"]) is not int or payload["schema_version"] != 2
            or type(payload["review_state"]) is not str or payload["review_state"] not in _STATES):
        _fail()
    # Reuse only the unchanged common manifest checks. This temporary projection
    # is neither returned nor hashed as the v2 identity; no v1 reference/scorer
    # or archived review is decoded through it.
    projected = {**payload, "schema_version": 1, "review_state": "draft" if payload["review_state"] == "draft"
                 else "historical_operator_declaration"}
    checked = legacy.validate_crop_pack_manifest(projected, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, page_count=page_count)
    checked.update(schema_version=2, review_state=payload["review_state"])
    return json.loads(legacy.encode_manifest(checked))


def _tokens(text):
    result = [] if text == "" else text.split("\n")
    if len(result) > 64 or any(not token.strip() or len(token) > 256 or "\r" in token for token in result):
        _fail()
    return result


def _reset(annotations):
    result = []
    for annotation in annotations:
        row = {**annotation, "raw_span": None}
        if row["status"] == "resolved":
            row.update(status="unresolved", resolution=None)
        result.append(row)
    return result


def _draft_shape(value):
    legacy._fields(value, {"reference", "critical_tokens_text", "uncertainty"})
    raw = legacy._draft({key: value[key] for key in ("reference", "critical_tokens_text")})
    uncertainty = value["uncertainty"]
    history._fields(uncertainty, {"journal", "annotations", "dirty"})
    if (type(uncertainty["dirty"]) is not bool or type(uncertainty["annotations"]) is not list
            or len(uncertainty["annotations"]) > history.MAX_ANNOTATIONS):
        _fail()
    for annotation in uncertainty["annotations"]:
        history._annotation_shape(annotation)
    if uncertainty["journal"] is None:
        if uncertainty["annotations"]:
            _fail()
    else:
        history._fields(uncertainty["journal"], history._JOURNAL_KEYS)
    raw["uncertainty"] = json.loads(history._bytes(uncertainty))
    return raw


def _draft_head(draft, declaration):
    uncertainty = draft["uncertainty"]
    expected = _reset(declaration["annotations"]) if uncertainty["dirty"] else declaration["annotations"]
    if history._bytes(uncertainty["annotations"]) != history._bytes(expected):
        _fail()
    if not uncertainty["dirty"] and (draft["reference"] != declaration["reference"]
            or _tokens(draft["critical_tokens_text"]) != declaration["critical_tokens"]):
        _fail()


def _draft_replay(draft, manifest, archives=None):
    uncertainty = draft["uncertainty"]
    if uncertainty["journal"] is None:
        return
    pin = manifest["pair"]["baseline"]
    if archives is None:
        replayed = history.replay_crop_journal_declaration(uncertainty["journal"],
            scope=manifest["scope"], anchor_report_sha256=pin["report_sha256"],
            anchor_region_id=pin["region_id"])
    else:
        replayed = history.replay_crop_journal(uncertainty["journal"],
            anchor_report=archives["baseline"]["report"], anchor_report_sha256=pin["report_sha256"])
    declaration = replayed["declaration"]
    if declaration["scope"] != manifest["scope"] or declaration["anchor"]["region_id"] != pin["region_id"]:
        _fail()
    _draft_head(draft, declaration)


def _review(raw, manifest):
    descriptor = manifest["files"]["review.json"]
    if type(raw) is not bytes or len(raw) != descriptor["bytes"] or legacy._sha(raw) != descriptor["sha256"]:
        _fail()
    value = legacy._decode(raw, MAX_REVIEW_BYTES)
    legacy._fields(value, {"schema_version", "kind", "draft", "reviewed"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 2
            or type(value["kind"]) is not str or value["kind"] != "ocr_crop_review_state"):
        _fail()
    value["draft"] = _draft_shape(value["draft"])
    saved = value["reviewed"]
    if saved is None:
        if manifest["review_state"] != "draft":
            _fail()
        return value
    legacy._fields(saved, {"reference", "comparison", "source_image"})
    for key, kind in (("reference", "ocr_crop_reference"), ("comparison", "ocr_crop_comparison")):
        item = saved[key]
        if (type(item) is not dict or type(item.get("schema_version")) is not int
                or item["schema_version"] != 2 or item.get("kind") != kind):
            _fail()
    history._bytes(saved["reference"])
    crops._bytes(saved["comparison"], comparison.MAX_RESULT_BYTES)
    outcome = saved["comparison"]
    scored = outcome.get("comparison") is not None
    if ("comparison" not in outcome or type(outcome["comparison"]) not in (dict, type(None))
            or outcome.get("status") != ("compared" if scored else "unavailable")
            or (outcome.get("reason") is not None if scored else outcome.get("reason") not in comparison._REASONS)
            or manifest["review_state"] != (_STATES[1] if scored else _STATES[2])):
        _fail()
    raster.validate_raster_view(saved["source_image"], scope=manifest["scope"])
    return value


def _review_replay(review, archives, manifest):
    saved = review["reviewed"]
    if saved is None:
        _draft_replay(review["draft"], manifest, archives)
        return
    draft = review["draft"]
    uncertainty = draft["uncertainty"]
    if uncertainty["dirty"] or uncertainty["journal"] is None:
        _fail()
    pair = manifest["pair"]
    # One v2 comparison performs the one full journal replay in this call.
    # Do not replay draft, validate reference, then compare that same history
    # again: reuse the declaration whose complete outcome just validated it.
    expected = comparison.compare_crop_candidates_v2(archives["baseline"]["report"],
        archives["retry"]["report"], saved["reference"],
        baseline_report_sha256=pair["baseline"]["report_sha256"], retry_report_sha256=pair["retry"]["report_sha256"],
        baseline_region_id=pair["baseline"]["region_id"], retry_region_id=pair["retry"]["region_id"])
    if crops._bytes(expected, comparison.MAX_RESULT_BYTES) != crops._bytes(saved["comparison"], comparison.MAX_RESULT_BYTES):
        _fail()
    reference = saved["reference"]
    if history._bytes(uncertainty["journal"]) != history._bytes(reference["journal"]):
        _fail()
    _draft_head(draft, reference)


def validate_crop_review_pack_v2(manifest, *, files, source_sha256, recovery_sha256, page_count):
    """Strict full original-archive replay; unresolved references never score."""
    manifest = validate_crop_pack_manifest_v2(manifest, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, page_count=page_count)
    descriptors, total = legacy._files(files)
    if descriptors != manifest["files"] or total != manifest["total_file_bytes"]:
        _fail()
    review = _review(files["review.json"], manifest)
    archives = legacy._archives(files, source_sha256=source_sha256, recovery_sha256=recovery_sha256, page_count=page_count)
    pair, scope = legacy._pair(archives, manifest["pair"]["baseline"]["region_id"], manifest["pair"]["retry"]["region_id"])
    if pair != manifest["pair"] or scope != manifest["scope"]:
        _fail()
    _review_replay(review, archives, manifest)
    return {"manifest": manifest, "review": review, "baseline": archives["baseline"], "retry": archives["retry"],
        "pack_sha256": legacy._sha(legacy.encode_manifest(manifest)), "validation_scope": VALIDATION_SCOPE,
        "requires_attention": True, "canonical_extraction_modified": False}


def recover_crop_pack_draft_v2(manifest, review_raw, *, source_sha256, recovery_sha256, page_count):
    """Recover bounded unverified authorship from exactly manifest/review bytes.

    Declared record/recipe/view hashes and resolution rows are not authenticated
    source history or restored adjudication authority. No report, candidate,
    scorer, renderer or current OCR runtime is used or returned.
    """
    manifest = validate_crop_pack_manifest_v2(manifest, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, page_count=page_count)
    review = _review(review_raw, manifest)
    _draft_replay(review["draft"], manifest)
    return {"draft": review["draft"], "scope": manifest["scope"],
        "pack_sha256": legacy._sha(legacy.encode_manifest(manifest)), "status": "unverified_saved_draft",
        "requires_attention": True, "canonical_extraction_modified": False}


def build_crop_review_pack_v2(*, baseline, retry, source_sha256, recovery_sha256, page_count,
                             baseline_region_id, retry_region_id, reference, critical_tokens_text,
                             uncertainty, reviewed=None, parent_pack_sha256=None):
    """Build a new explicit v2 pack; supplied review is historical declaration.

    Trusted host captures reviewed state; browsers may not supply metrics or
    consent. No default version inference or automatic journal genesis occurs.
    """
    files = {}
    for side, value in (("baseline", baseline), ("retry", retry)):
        legacy._fields(value, {"artifacts", "retained_inputs"})
        legacy._fields(value["artifacts"], legacy.ARTIFACT_LIMITS)
        inputs = value["retained_inputs"]
        if (type(inputs) is not dict or not len(legacy.INPUT_LIMITS) - 1 <= len(inputs) <= len(legacy.INPUT_LIMITS)
                or any(type(name) is not str for name in inputs)
                or not set(legacy.INPUT_LIMITS) - {"installation.json"} <= set(inputs)
                or not set(inputs) <= set(legacy.INPUT_LIMITS)):
            _fail()
        for group, values in (("artifacts", value["artifacts"]), ("inputs", inputs)):
            files.update({f"{side}/{group}/{name}": raw for name, raw in values.items()})
    draft = _draft_shape({"reference": reference, "critical_tokens_text": critical_tokens_text, "uncertainty": uncertainty})
    review = {"schema_version": 2, "kind": "ocr_crop_review_state", "draft": draft, "reviewed": reviewed}
    files["review.json"] = crops._bytes(review, MAX_REVIEW_BYTES)
    descriptors, total = legacy._files(files)
    archives = legacy._archives(files, source_sha256=source_sha256, recovery_sha256=recovery_sha256, page_count=page_count)
    pair, scope = legacy._pair(archives, baseline_region_id, retry_region_id)
    state = "draft"
    if reviewed is not None:
        legacy._fields(reviewed, {"reference", "comparison", "source_image"})
        if type(reviewed["comparison"]) is not dict:
            _fail()
        state = _STATES[1] if reviewed["comparison"].get("comparison") is not None else _STATES[2]
    manifest = {"schema_version": 2, "kind": "ocr_crop_review_pack", "source_sha256": source_sha256,
        "recovery_sha256": recovery_sha256, "page_count": page_count, "scope": scope, "pair": pair,
        "review_state": state, "files": descriptors, "total_file_bytes": total,
        "parent_pack_sha256": parent_pack_sha256, "validation_scope": VALIDATION_SCOPE,
        "requires_attention": True, "canonical_extraction_modified": False}
    manifest = validate_crop_pack_manifest_v2(manifest, source_sha256=source_sha256,
        recovery_sha256=recovery_sha256, page_count=page_count)
    _review_replay(_review(files["review.json"], manifest), archives, manifest)
    return {"manifest": manifest, "files": files}


def _parent_capture(snapshot):
    """Check the host-owned read_for_revision capture, not arbitrary testimony.

    The host already strictly verified its full archive/scoring evidence and
    must keep the selected-parent commit guard. Recheck captured raw identities
    and manifest/review equality without running historical scorers again.
    These hashes do not authenticate an independently fabricated capture.
    """
    legacy._fields(snapshot, {"evidence", "pack", "manifest_bytes"})
    pack, evidence = snapshot["pack"], snapshot["evidence"]
    legacy._fields(pack, {"manifest", "files"})
    legacy._fields(evidence, _EVIDENCE_KEYS)
    manifest = pack["manifest"]
    legacy._fields(manifest, legacy._MANIFEST_KEYS)
    version = manifest["schema_version"]
    if type(version) is not int or version not in (1, 2):
        _fail()
    validator = legacy.validate_crop_pack_manifest if version == 1 else validate_crop_pack_manifest_v2
    manifest = validator(manifest, **_binding(manifest))
    raw = snapshot["manifest_bytes"]
    if type(raw) is not bytes or raw != legacy.encode_manifest(manifest):
        _fail()
    descriptors, total = legacy._files(pack["files"])
    if descriptors != manifest["files"] or total != manifest["total_file_bytes"]:
        _fail()
    review = legacy._decode(pack["files"]["review.json"], MAX_REVIEW_BYTES)
    if (legacy.encode_manifest(evidence["manifest"]) != raw
            or crops._bytes(evidence["review"], MAX_REVIEW_BYTES) != crops._bytes(review, MAX_REVIEW_BYTES)
            or evidence["pack_sha256"] != legacy._sha(raw)
            or evidence["validation_scope"] != VALIDATION_SCOPE
            or evidence["requires_attention"] is not True or evidence["canonical_extraction_modified"] is not False):
        _fail()
    return manifest, review, pack["files"], legacy._sha(raw)


def validate_crop_pack_revision_v2(parent_evidence, child_pack):
    """Check a v2 child against a trusted full read_for_revision parent capture.

    Parent evidence is the host-only {evidence,pack,manifest_bytes} snapshot,
    never a browser argument. The complete child is strictly revalidated here;
    parent historical metrics are not recomputed or inherited as current consent.
    """
    parent, parent_review, parent_files, parent_sha = _parent_capture(parent_evidence)
    legacy._fields(child_pack, {"manifest", "files"})
    child = validate_crop_review_pack_v2(child_pack["manifest"], files=child_pack["files"], **_binding(parent))
    manifest = child["manifest"]
    if (manifest["parent_pack_sha256"] != parent_sha or manifest["pair"] != parent["pair"]
            or manifest["scope"] != parent["scope"]
            or set(child_pack["files"]) != set(parent_files)):
        _fail()
    for name, raw in parent_files.items():
        if name != "review.json" and child_pack["files"][name] != raw:
            _fail()
    current = child["review"]["draft"]["uncertainty"]
    if parent["schema_version"] == 1:
        if current["journal"] is None:
            _fail()  # Explicit valid genesis is required for a v1 upgrade.
        return child
    prior = parent_review["draft"]["uncertainty"]
    old, new = prior["journal"], current["journal"]
    if old is None:
        return child
    if new is None:
        _fail()
    for key in ("binding", "base", "base_declaration_sha256"):
        if history._bytes(old[key]) != history._bytes(new[key]):
            _fail()
    prefix = len(old["revisions"])
    if len(new["revisions"]) < prefix or history._bytes(new["revisions"][:prefix]) != history._bytes(old["revisions"]):
        _fail()
    old_views = {view["view_sha256"]: view for view in old["views"]}
    new_views = {view["view_sha256"]: view for view in new["views"]}
    if any(pin not in new_views or history._bytes(view) != history._bytes(new_views[pin]) for pin, view in old_views.items()):
        _fail()
    if prior["dirty"] and not current["dirty"]:
        if not any(revision["reset_anchors"] for revision in new["revisions"][prefix:]):
            _fail()
    return child
