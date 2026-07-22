import pytest

from operation_contracts import IndexOutcome


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
