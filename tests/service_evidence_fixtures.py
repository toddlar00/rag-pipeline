"""Generated evidence artifacts; no PDF parsing, OCR, embedding or network calls.

These fixtures exercise real provenance/quality/publication producers. Their
source bytes and conversion declaration are synthetic, not an actual conversion
receipt or evidence of image accuracy. Native integration must supply a separate
generated source binding and build the physical index with its real model.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import heading_lineage
import ocr_recovery
import quality_core
import rag
import retrieval_core
import service_contracts
import service_evidence_contracts
import source_fidelity_core as fidelity
import table_retrieval_core


MODEL = "nomic-ai/nomic-embed-text-v2-moe"
DEFAULT_TEXTS = (
    "The synthetic defendant is liable for the stated amount of 25 units.",
    "A synthetic second-page discussion concerns notice and a timely response.",
    "The synthetic third-page discussion preserves this distinct source context.",
    "A synthetic fourth-page discussion has deferred OCR evidence, not approval.",
    "The synthetic fifth-page discussion has no selected OCR retry for this page.",
)
SYNTHETIC_SOURCE = b"Synthetic source bytes: the defendant is NOT liable. No PDF or scan.\n"


def file_binding(path: Path) -> dict:
    raw = path.read_bytes()
    return {"name": path.name, "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def source_item(ref: str, text: str, page: int, *, label: str = "text") -> dict:
    return {"self_ref": ref, "label": label, "content_layer": "body", "text": text,
            "prov": [{"page_no": page, "charspan": [0, len(text)],
                      "bbox": {"l": 40, "t": 80, "r": 540, "b": 110,
                               "coord_origin": "TOPLEFT"}}]}


def lineaged_record(item: dict, index: int, *, text: str | None = None,
                    scope: tuple[int, ...] | None = None) -> dict:
    text = item["text"] if text is None else text
    descriptor, issues = fidelity.source_descriptor(item)
    assert not issues
    transform = fidelity.default_transform(item["label"])
    oracle = item["text"]
    tokens = fidelity.lexical_tokens(oracle)
    scope = tuple(range(len(descriptor["spans"]))) if scope is None else scope
    pages = [descriptor["spans"][number]["page"] for number in scope]
    entry = {"ref": item["self_ref"], "label": item["label"], "parent_refs": [],
             **descriptor, "scope": {"provenance_indexes": list(scope)},
             "transform": transform, "oracle_text_sha256": fidelity.text_sha256(oracle),
             "oracle_lexical_sha256": fidelity.lexical_sha256(tokens),
             "oracle_lexical_count": len(tokens), "recovery_sha256": None}
    if transform in fidelity.OPAQUE_TRANSFORMS:
        entry["recovery_sha256"] = fidelity.recovery_binding_sha256(
            ref=entry["ref"], transform=transform,
            source_text_sha256=descriptor["source_text_sha256"],
            oracle_text_sha256=entry["oracle_text_sha256"], source_binding=None)
    table = item["label"] == "table"
    metadata = {"chunk_index": index, "source_file": "synthetic.json",
                "source_lineage_schema_version": quality_core.SOURCE_LINEAGE_SCHEMA_VERSION,
                "source_items": [entry], "page_start": min(pages), "page_end": max(pages),
                "chapter_num": 1, "chapter_title": "Synthetic evidence",
                "section_path": "", "content_type": "table" if table else "author_narrative",
                "content_source": "table" if table else "body",
                "token_count": len(fidelity.lexical_tokens(text)),
                "embedding_token_count": len(fidelity.lexical_tokens(text)) + 8,
                "case_names": [], "primary_case": None}
    if table:
        metadata.update(table_rows=4, table_cols=2)
    record = {"text": text, "metadata": metadata}
    fidelity.attach_record_attestations([record])
    return record


def candidate(text: str) -> dict:
    lines = [] if not text else [{"text": text, "score": 0.99,
                                "box": [[0, 0], [100, 0], [100, 20], [0, 20]]}]
    return {"text": text, "lines": lines, "mean_confidence": 0.99 if text else None,
            "raster": {"width": 600, "height": 800, "dpi": 300,
                       "coordinate_system": "rendered_image_pixels"},
            "engine": {"name": "rapidocr", "version": "synthetic-no-execution",
                       "min_score": 0.0, "max_side": 6000}}


def recovery_report(source_sha256: str, page_count: int, *, page_offset: int = 0) -> dict:
    class Reader:
        def native_text(self, page_number):
            return "" if page_offset < page_number <= 4 + page_offset else DEFAULT_TEXTS[-1]

        def retry(self, page_number):
            page_number -= page_offset
            if page_number == 3:
                raise RuntimeError("synthetic retry failure")
            return candidate("The synthetic defendant is not liable for 25 units."
                             if page_number == 1 else "")

    reader = Reader()
    reader.page_count = page_count
    report = ocr_recovery.build_recovery_report(
        reader, source_sha256=source_sha256,
        policy=ocr_recovery.RetryPolicy(max_pages=3),
        requested_pages=tuple(range(1 + page_offset, min(3 + page_offset, page_count) + 1)))
    report["evidence_sha256"] = None
    return report


def build_bundle(root: Path, *, texts: tuple[str, ...] | None = None,
                 with_recovery: bool = True, with_table: bool = False,
                 with_multipage: bool = False, source: dict | None = None,
                 excluded_frontmatter: bool = False, preprocessed: bool = False,
                 embedding_model: str = MODEL, embedding_dimension: int = 4,
                 corpus_id: str = "synthetic", prepare_records=None) -> SimpleNamespace:
    """Build a complete isolated synthetic graph, with no physical vector index.

    ``source`` may bind a separately created generated PDF; this helper never
    opens it. The default binds declared synthetic bytes, not valid PDF content.
    Native embedding tests must replace the metadata-only manifest by calling
    the actual index producer on this new isolated directory. An optional local
    ``prepare_records(records)`` callback runs after final table/linkage metadata
    but before any artifact publication; native tests use it to freeze exact
    cached-tokenizer counts rather than the portable synthetic counts.
    """
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    docling_path = root / "synthetic.json"
    chunks_path = root / "synthetic_chunks.jsonl"
    markdown_path = root / "synthetic_docling.md"
    recovery_path = root / "recovery.json"
    db_path = root / "qdrant"
    db_path.mkdir()
    selected = DEFAULT_TEXTS if texts is None else texts
    offset = int(excluded_frontmatter)
    items = [source_item(f"#/texts/{i}", text, i + 1 + offset)
             for i, text in enumerate(selected)]
    document = {"name": "Synthetic evidence", "pages": {
        str(number): {"page_no": number, "size": {"width": 600, "height": 800}}
        for number in range(1, max(5, len(selected)) + 1 + offset)}, "texts": items,
        "tables": [], "pictures": [], "groups": [],
        "body": {"self_ref": "#/body", "children": [{"$ref": item["self_ref"]} for item in items]}}
    records = [lineaged_record(item, index) for index, item in enumerate(items)]
    if with_multipage:
        first = "A source item begins on physical page two. "
        second = "Its separate continuation occupies physical page five."
        item = source_item(f"#/texts/{len(items)}", first + second, 2 + offset)
        item["prov"][0]["charspan"] = [0, len(first)]
        item["prov"].append({"page_no": 5 + offset, "charspan": [len(first), len(first + second)],
                             "bbox": {"l": 40, "t": 160, "r": 540, "b": 190,
                                      "coord_origin": "TOPLEFT"}})
        item["prov"][0]["bbox"].update(t=160, b=190)
        items.append(item)
        document["body"]["children"].append({"$ref": item["self_ref"]})
        records.extend([lineaged_record(item, len(records), text=first.strip(), scope=(0,)),
                        lineaged_record(item, len(records) + 1, text=second, scope=(1,))])
    if with_table:
        text = "| Category | Units |\n| --- | --- |\n| Alpha | 25 |\n| Beta | 75 |\n| Gamma | 10 |\n| Gamma | 10 |"
        table = source_item("#/tables/0", text, 5 + offset, label="table")
        table["prov"][0]["bbox"].update(t=240, b=420)
        table["data"] = {"num_rows": 5, "num_cols": 2, "table_cells": []}
        document["tables"].append(table)
        document["body"]["children"].append({"$ref": table["self_ref"]})
        records.append(lineaged_record(table, len(records)))
    if excluded_frontmatter:
        contents = source_item(f"#/texts/{len(items)}", "Contents", 1, label="section_header")
        items.append(contents)
        document["body"]["children"].insert(0, {"$ref": contents["self_ref"]})
        entry = source_item(f"#/texts/{len(items)}", "Synthetic discussion ........ 2", 1)
        entry["prov"][0]["bbox"].update(t=160, b=190)
        items.append(entry)
        document["body"]["children"].insert(1, {"$ref": entry["self_ref"]})
    source = dict(source) if source is not None else {
        "name": "synthetic-source.bin", "size": len(SYNTHETIC_SOURCE),
        "sha256": hashlib.sha256(SYNTHETIC_SOURCE).hexdigest()}
    for record in records:
        record["metadata"][retrieval_core.STABLE_ID_SOURCE_FIELD] = copy.deepcopy(source)
    records.sort(key=lambda record: (record["metadata"]["page_start"],
                                    record["metadata"]["source_items"][0]["spans"][
                                        record["metadata"]["source_items"][0]["scope"][
                                            "provenance_indexes"][0]]["bbox"][1]))
    for index, record in enumerate(records):
        record["metadata"]["chunk_index"] = index
    structural_ranges = rag._book_structural_ranges(
        rag._identify_book_sections(document, emit_log=False))
    if excluded_frontmatter:
        assert (1, 1) in structural_ranges
    heading_lineage.attach_heading_bindings(document, records, structural_ranges=structural_ranges)
    records = table_retrieval_core.expand_table_records(
        records, stable_id_fn=retrieval_core._chunk_id,
        token_count_fn=lambda text: len(fidelity.lexical_tokens(text)))
    for index, record in enumerate(records):
        record["metadata"]["chunk_index"] = index
        record["metadata"]["embedding_token_count"] = len(fidelity.lexical_tokens(record["text"])) + 8
    retrieval_core._attach_retrieval_linkage(records)
    if prepare_records is not None:
        prepare_records(records)
    rag._atomic_write_json(docling_path, document)
    rag._atomic_write_text(markdown_path, "\n\n".join(record["text"] for record in records))
    conversion_path = rag._artifact_completion_path(docling_path, stage="conversion")
    outputs = {"docling_json": docling_path, "docling_markdown": markdown_path}
    effective_input = {"kind": "original", **source}
    if preprocessed:
        transformed = root / "synthetic-preprocessed.bin"
        rag._atomic_write_text(transformed, "Synthetic transformed bytes, no coordinate inverse or PDF.")
        outputs["preprocessed_pdf"] = transformed
        effective_input = {"kind": "preprocessed", **file_binding(transformed)}
    rag._write_artifact_completion(
        conversion_path, stage="conversion", source_sha256=source["sha256"],
        source_name=source["name"], source_record_count=None,
        parameters={"fixture": "synthetic-evidence-v1"},
        outputs=outputs,
        schema_version=rag.CONVERSION_COMPLETION_SCHEMA_VERSION,
        extra_fields={"source": {**source, "capture_policy": "stream-copy-v1"},
                      "effective_input": effective_input})
    rag._atomic_write_jsonl(chunks_path, records)
    parameters = {"embedding_model": embedding_model,
                  "structure_profile": rag._document_profiles.profile_provenance(
                      rag._document_profiles.get_profile(rag.DEFAULT_STRUCTURE_PROFILE)),
                  "stable_id_policy_version": retrieval_core.STABLE_ID_SCHEMA_VERSION}
    upstream = {"docling_json": file_binding(docling_path),
                "conversion_manifest": {"name": conversion_path.name,
                    "sha256": file_binding(conversion_path)["sha256"],
                    "schema_version": rag.CONVERSION_COMPLETION_SCHEMA_VERSION},
                "table_recovery": None}
    oracles = {}
    for record in records:
        for entry in record["metadata"]["source_items"]:
            if entry["transform"] not in fidelity.OPAQUE_TRANSFORMS:
                continue
            item = document["tables"][0]
            oracles[entry["ref"]] = {key: entry[key] for key in (
                "transform", "source_text_sha256", "oracle_text_sha256",
                "oracle_lexical_sha256", "oracle_lexical_count", "recovery_sha256")}
            oracles[entry["ref"]].update(
                oracle_lexical_tokens=list(fidelity.lexical_tokens(item["text"])),
                oracle_group_sha256=fidelity.single_source_oracle_group_sha256(entry["ref"]),
                oracle_group_members=[entry["ref"]])
    oracle_path = fidelity.source_oracle_registry_path(chunks_path)
    rag._atomic_write_json(oracle_path, fidelity.build_source_oracle_registry(
        oracles=oracles, input_bindings=upstream))
    inputs = {**upstream, "source_fidelity_oracles": rag._source_oracle_registry_binding(oracle_path)}
    completion_path = rag._artifact_completion_path(chunks_path, stage="chunking")
    rag._write_artifact_completion(
        completion_path, stage="chunking", source_sha256=upstream["docling_json"]["sha256"],
        source_record_count=None, parameters=parameters,
        outputs={"chunks_jsonl": chunks_path, "source_fidelity_oracles": oracle_path},
        schema_version=rag.CHUNK_COMPLETION_SCHEMA_VERSION,
        extra_fields={"inputs": inputs, "structure_profile": parameters["structure_profile"],
                      "structure_profile_parameters_sha256": rag._structure_profile_parameters_binding(
                          rag._artifact_parameters_sha256(parameters), parameters["structure_profile"])})
    quality = rag._publish_corpus_quality_report(docling_path, chunks_path, parameters=parameters)
    quality_path = quality_core.quality_report_path(chunks_path)
    manifest_path = rag._save_index_manifest(
        db_path, backend="qdrant", collection_name=corpus_id, embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        chunk_hashes={retrieval_core._chunk_id(record): retrieval_core._chunk_hash(record) for record in records},
        source_sha256=file_binding(chunks_path)["sha256"], source_record_count=len(records),
        quality_report_schema_version=quality_core.QUALITY_REPORT_SCHEMA_VERSION,
        quality_report_sha256=file_binding(quality_path)["sha256"],
        table_child_count=table_retrieval_core.table_child_count(records))
    report = recovery_report(source["sha256"], len(document["pages"]), page_offset=offset) if with_recovery else None
    if report is not None:
        rag._atomic_write_json(recovery_path, report)
    config = service_contracts.CorpusConfig(
        corpus_id=corpus_id, db_path=db_path, chunks_path=chunks_path,
        collection_name=corpus_id, embedding_model=embedding_model,
        search_timeout_seconds=120)
    evidence_config = service_evidence_contracts.EvidenceCorpusConfig(
        corpus_id=corpus_id, docling_path=docling_path,
        recovery_path=recovery_path if with_recovery else None)
    return SimpleNamespace(root=root, source=source, docling_path=docling_path,
        chunks_path=chunks_path, markdown_path=markdown_path, conversion_path=conversion_path,
        completion_path=completion_path, oracle_path=oracle_path, quality_path=quality_path,
        recovery_path=recovery_path if with_recovery else None, manifest_path=manifest_path,
        db_path=db_path, records=records, document=document, config=config, evidence_config=evidence_config,
        parameters=parameters, quality=quality, report=report,
        structural_ranges=structural_ranges,
        manifest=json.loads(manifest_path.read_text(encoding="utf-8")))


def physical_binding(*, search_index=None):
    """Snapshot production collaborators; tests may inject only retrieval."""
    from service_evidence_search import EvidenceSearchBinding
    return EvidenceSearchBinding(
        search_index=rag.search_index if search_index is None else search_index,
        vector_store_lock=rag._vector_store_lock, chunk_output_lease=rag._chunk_output_lease,
        chunk_id=rag._chunk_id, chunk_hash=rag._chunk_hash,
        validated_quality_report_binding=rag._validated_quality_report_binding,
        load_conversion_source_binding=rag._load_conversion_source_binding,
        identify_book_sections=rag._identify_book_sections,
        book_structural_ranges=rag._book_structural_ranges,
        manifest_schema_version=rag.INDEX_MANIFEST_SCHEMA_VERSION,
        embedding_input_policy_version=rag.EMBEDDING_INPUT_POLICY_VERSION,
        model_artifact_lock_sha256=rag._model_artifact_lock_sha256())
