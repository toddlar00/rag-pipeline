import pytest

from operation_contracts import IndexOperationMetrics, IndexOutcome


def test_index_outcome_exposes_content_free_metrics():
    outcome = IndexOutcome(
        backend="chroma", disposition="updated", total_records=10,
        changed_records=3, unchanged_records=7, removed_records=2,
        upserted_records=3, batch_count=1, physical_count=10,
        committed=True)

    assert outcome.telemetry_metrics() == {
        "total_records": 10,
        "changed_records": 3,
        "unchanged_records": 7,
        "removed_records": 2,
        "upserted_records": 3,
        "batch_count": 1,
        "physical_count": 10,
        "committed": True,
        "created": False,
        "rebuilt": False,
        "updated": True,
        "unchanged": False,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"backend": "unknown"},
        {"disposition": "partial"},
        {"changed_records": -1, "unchanged_records": 11},
        {"physical_count": 9},
        {"committed": 1},
        {"committed": False},
        {"disposition": "unchanged", "changed_records": 1,
         "unchanged_records": 9, "upserted_records": 1,
         "batch_count": 1},
        {"disposition": "created", "changed_records": 9,
         "unchanged_records": 1, "upserted_records": 9,
         "batch_count": 1},
        {"disposition": "updated"},
        {"upserted_records": 1, "batch_count": 1},
        {"changed_records": 1, "unchanged_records": 9,
         "upserted_records": 1, "batch_count": 0},
    ],
)
def test_index_outcome_rejects_ambiguous_or_uncommitted_shapes(overrides):
    values = {
        "backend": "qdrant", "disposition": "unchanged",
        "total_records": 10, "changed_records": 0,
        "unchanged_records": 10, "removed_records": 0,
        "upserted_records": 0, "batch_count": 0,
        "physical_count": 10, "committed": True,
    }
    values.update(overrides)
    with pytest.raises((TypeError, ValueError)):
        IndexOutcome(**values)


@pytest.mark.parametrize("disposition", ["created", "rebuilt"])
def test_complete_replacement_outcomes_are_coherent(disposition):
    outcome = IndexOutcome(
        backend="chroma", disposition=disposition, total_records=2,
        changed_records=2, unchanged_records=0, removed_records=0,
        upserted_records=2, batch_count=1, physical_count=2,
        committed=True)

    assert outcome.disposition == disposition


def test_removal_only_update_is_coherent():
    outcome = IndexOutcome(
        backend="qdrant", disposition="updated", total_records=2,
        changed_records=0, unchanged_records=2, removed_records=1,
        upserted_records=0, batch_count=0, physical_count=2,
        committed=True)

    assert outcome.removed_records == 1


def test_index_operation_metrics_extend_telemetry_without_content():
    operations = IndexOperationMetrics(
        collection_delete_calls=0,
        collection_create_calls=0,
        record_delete_calls=1,
        upsert_calls=1,
        queue_put_count=1,
        queue_saturation_events=2,
        queue_wait_ms=12.3456,
    )
    outcome = IndexOutcome(
        backend="qdrant", disposition="updated", total_records=10,
        changed_records=3, unchanged_records=7, removed_records=2,
        upserted_records=3, batch_count=1, physical_count=10,
        committed=True, operations=operations)

    metrics = outcome.telemetry_metrics()
    assert metrics["physical_mutation_calls"] == 2
    assert metrics["record_delete_calls"] == 1
    assert metrics["upsert_calls"] == 1
    assert metrics["queue_put_count"] == 1
    assert metrics["queue_saturation_events"] == 2
    assert metrics["queue_wait_ms"] == 12.346
    assert "path" not in metrics
    assert "record" not in metrics


@pytest.mark.parametrize("operations", [
    IndexOperationMetrics(
        collection_delete_calls=0, collection_create_calls=0,
        record_delete_calls=0, upsert_calls=0, queue_put_count=1,
        queue_saturation_events=0, queue_wait_ms=0),
    IndexOperationMetrics(
        collection_delete_calls=0, collection_create_calls=1,
        record_delete_calls=0, upsert_calls=1, queue_put_count=1,
        queue_saturation_events=0, queue_wait_ms=0),
])
def test_index_operation_metrics_must_match_logical_outcome(operations):
    with pytest.raises(ValueError):
        IndexOutcome(
            backend="chroma", disposition="updated", total_records=2,
            changed_records=1, unchanged_records=1, removed_records=0,
            upserted_records=1, batch_count=1, physical_count=2,
            committed=True, operations=operations)


def test_unchanged_outcome_accepts_only_a_zero_operation_contract():
    operations = IndexOperationMetrics(
        collection_delete_calls=0, collection_create_calls=0,
        record_delete_calls=0, upsert_calls=0, queue_put_count=0,
        queue_saturation_events=0, queue_wait_ms=0)
    outcome = IndexOutcome(
        backend="chroma", disposition="unchanged", total_records=2,
        changed_records=0, unchanged_records=2, removed_records=0,
        upserted_records=0, batch_count=0, physical_count=2,
        committed=True, operations=operations)

    assert outcome.operations == operations


@pytest.mark.parametrize(
    ("backend", "disposition", "removed_records", "operations"),
    [
        ("chroma", "created", 0, IndexOperationMetrics(
            collection_delete_calls=0, collection_create_calls=1,
            record_delete_calls=1, upsert_calls=1, queue_put_count=1,
            queue_saturation_events=0, queue_wait_ms=0)),
        ("qdrant", "rebuilt", 0, IndexOperationMetrics(
            collection_delete_calls=1, collection_create_calls=1,
            record_delete_calls=1, upsert_calls=1, queue_put_count=1,
            queue_saturation_events=0, queue_wait_ms=0)),
        ("chroma", "updated", 1, IndexOperationMetrics(
            collection_delete_calls=0, collection_create_calls=0,
            record_delete_calls=0, upsert_calls=1, queue_put_count=1,
            queue_saturation_events=0, queue_wait_ms=0)),
        ("qdrant", "updated", 0, IndexOperationMetrics(
            collection_delete_calls=0, collection_create_calls=0,
            record_delete_calls=0, upsert_calls=1, queue_put_count=0,
            queue_saturation_events=0, queue_wait_ms=0)),
    ],
)
def test_exact_operation_contract_rejects_impossible_physical_calls(
        backend, disposition, removed_records, operations):
    with pytest.raises(ValueError):
        IndexOutcome(
            backend=backend, disposition=disposition, total_records=2,
            changed_records=2, unchanged_records=0,
            removed_records=removed_records,
            upserted_records=2, batch_count=1, physical_count=2,
            committed=True, operations=operations)


def test_parallel_chroma_may_upsert_without_the_bounded_queue():
    operations = IndexOperationMetrics(
        collection_delete_calls=0, collection_create_calls=0,
        record_delete_calls=0, upsert_calls=1, queue_put_count=0,
        queue_saturation_events=0, queue_wait_ms=0)

    outcome = IndexOutcome(
        backend="chroma", disposition="updated", total_records=2,
        changed_records=1, unchanged_records=1, removed_records=0,
        upserted_records=1, batch_count=1, physical_count=2,
        committed=True, operations=operations)

    assert outcome.operations.queue_put_count == 0


@pytest.mark.parametrize("record_delete_calls", [2, 99])
def test_qdrant_update_allows_at_most_one_batched_point_delete(
        record_delete_calls):
    operations = IndexOperationMetrics(
        collection_delete_calls=0, collection_create_calls=0,
        record_delete_calls=record_delete_calls, upsert_calls=1,
        queue_put_count=1, queue_saturation_events=0, queue_wait_ms=0)

    with pytest.raises(ValueError, match="at most one"):
        IndexOutcome(
            backend="qdrant", disposition="updated", total_records=2,
            changed_records=1, unchanged_records=1, removed_records=0,
            upserted_records=1, batch_count=1, physical_count=2,
            committed=True, operations=operations)


def test_append_only_qdrant_update_requires_no_point_delete():
    operations = IndexOperationMetrics(
        collection_delete_calls=0, collection_create_calls=0,
        record_delete_calls=0, upsert_calls=1, queue_put_count=1,
        queue_saturation_events=0, queue_wait_ms=0)

    outcome = IndexOutcome(
        backend="qdrant", disposition="updated", total_records=2,
        changed_records=1, unchanged_records=1, removed_records=0,
        upserted_records=1, batch_count=1, physical_count=2,
        committed=True, operations=operations)

    assert outcome.operations.record_delete_calls == 0
