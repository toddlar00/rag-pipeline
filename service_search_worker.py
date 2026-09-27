"""Hidden child-process composition root for one local service search."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import rag
import service_runtime
import service_evidence_runtime


def _silence_worker_output() -> None:
    logging.disable(logging.CRITICAL)
    try:
        sink = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        return
    sys.stdout = sink
    sys.stderr = sink


def _execute_evidence(config, evidence, request, request_id, *, security_policy):
    # Physical retrieval and artifact validation belong only in this contained
    # child shell.  Binding creation is inside the worker's redaction guard.
    import service_evidence_search

    binding = service_evidence_search.EvidenceSearchBinding(
        search_index=rag.search_index,
        vector_store_lock=rag._vector_store_lock,
        chunk_output_lease=rag._chunk_output_lease,
        chunk_id=rag._chunk_id,
        chunk_hash=rag._chunk_hash,
        validated_quality_report_binding=rag._validated_quality_report_binding,
        load_conversion_source_binding=rag._load_conversion_source_binding,
        identify_book_sections=rag._identify_book_sections,
        book_structural_ranges=rag._book_structural_ranges,
        manifest_schema_version=rag.INDEX_MANIFEST_SCHEMA_VERSION,
        embedding_input_policy_version=rag.EMBEDDING_INPUT_POLICY_VERSION,
        model_artifact_lock_sha256=rag._model_artifact_lock_sha256())
    return service_evidence_search.execute_evidence_search(
        config, evidence, request, request_id, binding=binding,
        security_policy=security_policy)


def main(argv: Sequence[str] | None = None) -> int:
    """Run only the private search-worker action with two file paths."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (len(arguments) != 3
            or arguments[0] not in {service_runtime._SEARCH_WORKER_ACTION,
                                    service_evidence_runtime.EVIDENCE_WORKER_ACTION}):
        return 2
    _silence_worker_output()
    if arguments[0] == service_evidence_runtime.EVIDENCE_WORKER_ACTION:
        return service_evidence_runtime.evidence_worker_main(
            Path(arguments[1]), Path(arguments[2]), execute_fn=_execute_evidence)
    return service_runtime.search_worker_main(
        Path(arguments[1]),
        Path(arguments[2]),
        search_index_fn=rag.search_index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
