"""Direct tests for backend-neutral vector mutation lifecycle policy."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

import vector_lifecycle as lifecycle


def test_vector_lifecycle_is_a_standard_library_leaf_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import vector_lifecycle; "
                "forbidden = {'rag', 'index_state', 'operation_contracts', "
                "'requests', 'chromadb', 'qdrant_client', 'numpy', 'torch'}; "
                "loaded = forbidden.intersection(sys.modules); "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("backend", "marker_path_name", "begin_name"),
    [
        ("chroma", "_chroma_update_marker_path",
         "_begin_chroma_index_update"),
        ("qdrant", "_qdrant_update_marker_path",
         "_begin_qdrant_index_update"),
    ],
)
def test_rag_lifecycle_facade_resolves_collaborators_at_call_time(
        monkeypatch, tmp_path, backend, marker_path_name, begin_name):
    import rag

    marker_path = tmp_path / f"{backend}.updating.json"
    state = {"token": None}
    events = []
    monkeypatch.setattr(
        rag, marker_path_name,
        lambda *_args, **_kwargs: marker_path)
    monkeypatch.setattr(
        rag, "_index_update_marker_owned_by",
        lambda _path, token, **_kwargs: (
            token == state["token"] and marker_path.is_file()))
    monkeypatch.setattr(
        rag, "uuid4", lambda: SimpleNamespace(hex="late-token"))

    update = rag._new_vector_update_lifecycle(
        tmp_path,
        backend=backend,
        collection_name="book",
        source_sha256="source",
        source_record_count=1,
        target_ids={"record"},
        active_update_token=None,
    )

    def begin(db_dir, **kwargs):
        events.append(("begin", db_dir, kwargs))
        state["token"] = kwargs["owner_token"]
        marker_path.write_text(state["token"], encoding="utf-8")
        return marker_path

    def finish(path, **kwargs):
        events.append(("finish", path, kwargs))
        marker_path.unlink()

    # Patch after composition: the closures must still resolve the facade's
    # current collaborators when mutation and commit actually occur.
    monkeypatch.setattr(rag, begin_name, begin)
    monkeypatch.setattr(rag, "_finish_index_update", finish)

    update.mutate(lambda: events.append("mutation"))
    receipt = update.verify({"record"}, lambda _expected: "verified")
    update.commit(
        receipt,
        close_client_fn=lambda: events.append("close"),
        save_manifest_fn=lambda: events.append("save"),
    )

    assert events[0][0] == "begin"
    assert events[0][1] == tmp_path
    assert events[0][2]["owner_token"] == "late-token"
    assert events[0][2]["replace_existing"] is False
    assert events[1:4] == ["mutation", "close", "save"]
    assert events[4][0] == "finish"
    assert events[4][1] == marker_path
    assert not marker_path.exists()


@pytest.mark.parametrize(
    ("new_items", "old_hashes", "changed", "removed", "replaced"),
    [
        ([('a', 'a', 'h1')], {"a": "h1"}, (), (), ()),
        (
            [('a', 'a', 'h1'), ('b', 'b', 'h2')],
            {"a": "h1"},
            ("b",),
            (),
            (),
        ),
        ([('a', 'a', 'h2')], {"a": "h1"}, ("a",), (), ("a",)),
        ([('a', 'a', 'h1')], {"a": "h1", "b": "h2"}, (), ("b",), ()),
        (
            [('a', 'a', 'h2'), ('c', 'c', 'h3')],
            {"a": "h1", "b": "h2"},
            ("a", "c"),
            ("b",),
            ("a",),
        ),
    ],
)
def test_plan_reconciliation_covers_incremental_shapes(
        new_items, old_hashes, changed, removed, replaced):
    plan = lifecycle.plan_reconciliation(new_items, old_hashes)

    assert tuple(item.stable_id for item in plan.changed_items) == changed
    assert plan.removed_ids == removed
    assert plan.changed_existing_ids == replaced
    assert plan.deletion_ids == removed + replaced
    assert plan.changed_count == len(changed)
    assert plan.unchanged_count == len(new_items) - len(changed)
    assert plan.removed_count == len(removed)


def test_plan_is_ordered_and_rejects_duplicate_new_stable_ids():
    plan = lifecycle.plan_reconciliation(
        [(1, "z", "new-z"), (2, "a", "new-a")],
        {"b": "old-b", "z": "old-z"},
    )

    assert plan.removed_ids == ("b",)
    assert plan.changed_existing_ids == ("z",)
    assert plan.deletion_ids == ("b", "z")
    assert plan.expected_after_delete == frozenset()
    assert plan.target_ids == frozenset({"z", "a"})
    with pytest.raises(ValueError, match="duplicate stable ID"):
        lifecycle.plan_reconciliation(
            [(1, "same", "h1"), (2, "same", "h2")], {})


def test_plan_repairs_legacy_empty_old_hash_as_changed_existing():
    plan = lifecycle.plan_reconciliation(
        [("record", "stable", "current-hash")],
        {"stable": ""},
    )

    assert tuple(item.stable_id for item in plan.changed_items) == ("stable",)
    assert plan.changed_existing_ids == ("stable",)
    assert plan.deletion_ids == ("stable",)


class MarkerHarness:
    def __init__(self, tmp_path, events, *, initial_token=None,
                 finish_error=None):
        self.path = tmp_path / "collection.updating.json"
        self.events = events
        self.token = initial_token
        self.finish_error = finish_error
        if initial_token is not None:
            self.path.write_text(initial_token, encoding="utf-8")

    def owns(self, token):
        return (
            isinstance(token, str)
            and token == self.token
            and self.path.is_file()
        )

    def begin(self, token, replace_existing):
        self.events.append(("begin", token, replace_existing))
        self.token = token
        self.path.write_text(token, encoding="utf-8")

    def finish(self, token):
        self.events.append(("finish", token))
        if self.finish_error is not None:
            raise self.finish_error
        assert self.owns(token)
        self.path.unlink()
        self.token = None


def _new_lifecycle(tmp_path, events, *, target_ids=("new",),
                   initial_token=None, finish_error=None,
                   begin_fn=None, token="generated-token", marker=None):
    marker = marker or MarkerHarness(
        tmp_path, events, initial_token=initial_token,
        finish_error=finish_error)
    guard = lifecycle.UpdateGuard(
        backend="chroma",
        collection_name="book",
        marker_path=marker.path,
        active_token=initial_token,
        marker_owned_fn=marker.owns,
        begin_update_fn=begin_fn or marker.begin,
        finish_update_fn=marker.finish,
        token_factory=lambda: token,
    )
    return lifecycle.VectorUpdateLifecycle(
        target_ids=target_ids, guard=guard), marker


def test_owned_marker_is_reused_and_revalidated_without_replacement(tmp_path):
    events = []
    update, marker = _new_lifecycle(
        tmp_path, events, initial_token="existing")

    update.prepare_mutation()
    update.mutate(lambda: events.append("mutation"))

    assert events == ["mutation"]
    assert update.guard.token == "existing"
    assert marker.path.is_file()


def test_foreign_marker_is_replaced_before_first_mutation(tmp_path):
    events = []
    update, marker = _new_lifecycle(tmp_path, events)
    marker.token = "foreign"
    marker.path.write_text("foreign", encoding="utf-8")

    update.mutate(lambda: events.append("mutation"))

    assert events == [
        ("begin", "generated-token", True),
        "mutation",
    ]
    assert update.guard.token == "generated-token"


def test_marker_begin_failure_prevents_mutation_and_can_be_retried(tmp_path):
    events = []
    attempts = 0
    marker = MarkerHarness(tmp_path, events)

    def begin(token, replace_existing):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("marker write failed")
        marker.begin(token, replace_existing)

    update, _marker = _new_lifecycle(
        tmp_path, events, begin_fn=begin, marker=marker)

    with pytest.raises(OSError, match="marker write failed"):
        update.mutate(lambda: pytest.fail("mutation must not run"))
    assert update.guard.token is None

    update.mutate(lambda: events.append("mutation"))
    assert events[-1] == "mutation"


def test_silent_marker_begin_failure_is_detected_before_mutation(tmp_path):
    update, _marker = _new_lifecycle(
        tmp_path, [], begin_fn=lambda _token, _replace: None)

    with pytest.raises(RuntimeError, match="ownership changed"):
        update.mutate(lambda: pytest.fail("mutation must not run"))


def test_marker_ownership_loss_prevents_later_mutation(tmp_path):
    events = []
    update, marker = _new_lifecycle(tmp_path, events)
    update.mutate(lambda: events.append("first"))
    marker.token = "replacement"
    marker.path.write_text("replacement", encoding="utf-8")

    with pytest.raises(RuntimeError, match="ownership changed"):
        update.mutate(lambda: pytest.fail("second mutation must not run"))


def _reconcile(
        tmp_path, *, new_items, old_hashes, initial_rows,
        collection_exists=True, rebuild=False, finish_error=None):
    events = []
    plan = lifecycle.plan_reconciliation(new_items, old_hashes)
    update, marker = _new_lifecycle(
        tmp_path, events, target_ids=plan.target_ids,
        finish_error=finish_error)
    state = {"rows": dict(initial_rows), "exists": collection_exists}

    def delete_collection():
        events.append("delete_collection")
        state["rows"].clear()
        state["exists"] = False

    def create_collection():
        events.append("create_collection")
        state["exists"] = True
        return state

    def verify(handle, expected):
        events.append(("verify", expected))
        assert handle is state
        if frozenset(state["rows"]) != expected:
            raise RuntimeError("physical IDs differ")
        return tuple(sorted(state["rows"]))

    def delete_ids(handle, stable_ids, _verified):
        events.append(("delete", stable_ids))
        assert handle is state
        for stable_id in stable_ids:
            state["rows"].pop(stable_id, None)

    reconciled = lifecycle.reconcile_collection(
        lifecycle=update,
        plan=plan,
        handle=state if collection_exists else None,
        collection_exists=collection_exists,
        rebuild_collection=rebuild,
        delete_collection_fn=delete_collection,
        create_collection_fn=create_collection,
        verify_stable_ids_fn=verify,
        delete_stable_ids_fn=delete_ids,
    )
    return plan, update, marker, state, reconciled, events, verify


def test_unchanged_reconciliation_commits_without_dirty_marker(tmp_path):
    (plan, update, marker, _state, reconciled,
     events, _verify) = _reconcile(
        tmp_path,
        new_items=[("record", "same", "h1")],
        old_hashes={"same": "h1"},
        initial_rows={"same": "old"},
    )

    update.commit(
        reconciled.receipt,
        close_client_fn=lambda: events.append("close"),
        save_manifest_fn=lambda: events.append("save"),
    )

    assert plan.changed_count == 0
    assert events == [
        ("verify", frozenset({"same"})),
        "close",
        "save",
    ]
    assert not marker.path.exists()


def test_removal_only_reconciliation_orders_guard_and_commit(tmp_path):
    (_plan, update, marker, state, reconciled,
     events, _verify) = _reconcile(
        tmp_path,
        new_items=[("record", "keep", "h1")],
        old_hashes={"keep": "h1", "remove": "h2"},
        initial_rows={"keep": "old", "remove": "obsolete"},
    )

    update.commit(
        reconciled.receipt,
        close_client_fn=lambda: events.append("close"),
        save_manifest_fn=lambda: events.append("save"),
    )

    assert state["rows"] == {"keep": "old"}
    assert events == [
        ("verify", frozenset({"keep", "remove"})),
        ("begin", "generated-token", False),
        ("delete", ("remove",)),
        ("verify", frozenset({"keep"})),
        "close",
        "save",
        ("finish", "generated-token"),
    ]
    assert not marker.path.exists()


def test_rebuild_and_upsert_require_final_postmutation_verification(tmp_path):
    (plan, update, _marker, state, reconciled,
     events, verify) = _reconcile(
        tmp_path,
        new_items=[("record", "new", "h2")],
        old_hashes={},
        initial_rows={"old": "h1"},
        rebuild=True,
    )
    assert reconciled.receipt is None

    update.prepare_mutation()
    update.mutate(
        lambda: (events.append(("upsert", "new")),
                 state["rows"].update({"new": "h2"})))
    with pytest.raises(RuntimeError, match="must be verified"):
        update.commit(
            lifecycle.VerificationReceipt(
                object(), 0, plan.target_ids, None),
            close_client_fn=lambda: events.append("close"),
            save_manifest_fn=lambda: events.append("save"),
        )
    receipt = update.verify(
        plan.target_ids, lambda expected: verify(state, expected))
    update.commit(
        receipt,
        close_client_fn=lambda: events.append("close"),
        save_manifest_fn=lambda: events.append("save"),
    )

    assert events == [
        ("begin", "generated-token", False),
        "delete_collection",
        "create_collection",
        ("upsert", "new"),
        ("verify", frozenset({"new"})),
        "close",
        "save",
        ("finish", "generated-token"),
    ]


@pytest.mark.parametrize("failure_stage", ["close", "save", "finish"])
def test_commit_failure_matrix_preserves_dirty_marker(
        tmp_path, failure_stage):
    failure = RuntimeError(f"{failure_stage} failed")
    (plan, update, marker, state, _reconciled,
     events, verify) = _reconcile(
        tmp_path,
        new_items=[("record", "new", "h2")],
        old_hashes={"new": "h1"},
        initial_rows={"new": "h1"},
        finish_error=failure if failure_stage == "finish" else None,
    )
    update.mutate(lambda: state["rows"].update({"new": "h2"}))
    receipt = update.verify(
        plan.target_ids, lambda expected: verify(state, expected))

    def close():
        events.append("close")
        if failure_stage == "close":
            raise failure

    def save():
        events.append("save")
        if failure_stage == "save":
            raise failure

    with pytest.raises(RuntimeError) as raised:
        update.commit(
            receipt, close_client_fn=close, save_manifest_fn=save)

    assert raised.value is failure
    assert marker.path.is_file()
    assert not update.committed
    if failure_stage == "close":
        assert events[-1] == "close"
        assert "save" not in events
    elif failure_stage == "save":
        assert events[-1] == "save"
        assert not any(
            isinstance(event, tuple) and event[0] == "finish"
            for event in events)
    else:
        assert events[-2:] == ["save", ("finish", "generated-token")]


def test_receipt_is_invalidated_by_a_later_mutation(tmp_path):
    events = []
    update, _marker = _new_lifecycle(tmp_path, events)
    update.mutate(lambda: events.append("first"))
    receipt = update.verify(
        {"new"}, lambda expected: tuple(sorted(expected)))
    update.mutate(lambda: events.append("second"))

    with pytest.raises(RuntimeError, match="must be verified"):
        update.commit(
            receipt,
            close_client_fn=lambda: events.append("close"),
            save_manifest_fn=lambda: events.append("save"),
        )


def test_marker_loss_after_verification_prevents_close_and_manifest(tmp_path):
    events = []
    update, marker = _new_lifecycle(tmp_path, events)
    update.mutate(lambda: events.append("mutation"))
    receipt = update.verify({"new"}, lambda _expected: "verified")
    marker.path.unlink()

    with pytest.raises(RuntimeError, match="ownership changed"):
        update.commit(
            receipt,
            close_client_fn=lambda: events.append("close"),
            save_manifest_fn=lambda: events.append("save"),
        )

    # Verification is followed by close only after marker ownership is still
    # proven.  The old manifest therefore remains authoritative.
    assert "close" not in events
    assert "save" not in events


def test_mutation_callback_cannot_reenter_lifecycle(tmp_path):
    events = []
    update, marker = _new_lifecycle(tmp_path, events)

    def outer_mutation():
        update.mutate(lambda: events.append("unverified nested mutation"))

    with pytest.raises(RuntimeError, match="reentry.*mutation"):
        update.mutate(outer_mutation)

    assert "unverified nested mutation" not in events
    assert marker.path.is_file()
    assert not update.committed


def test_verification_callback_cannot_mutate_and_stamp_stale_evidence(
        tmp_path):
    events = []
    update, marker = _new_lifecycle(tmp_path, events)
    update.mutate(lambda: events.append("initial mutation"))

    def verify_and_mutate(_expected):
        update.mutate(lambda: events.append("unverified nested mutation"))
        return "stale evidence"

    with pytest.raises(RuntimeError, match="reentry.*verification"):
        update.verify({"new"}, verify_and_mutate)

    assert "unverified nested mutation" not in events
    assert marker.path.is_file()
    assert not update.committed


@pytest.mark.parametrize("reentry_stage", ["close", "save"])
def test_commit_callbacks_cannot_mutate_after_receipt_validation(
        tmp_path, reentry_stage):
    events = []
    update, marker = _new_lifecycle(tmp_path, events)
    update.mutate(lambda: events.append("initial mutation"))
    receipt = update.verify({"new"}, lambda _expected: "verified")

    def reenter():
        events.append(reentry_stage)
        update.mutate(lambda: events.append("unverified nested mutation"))

    with pytest.raises(RuntimeError, match="reentry.*commit"):
        update.commit(
            receipt,
            close_client_fn=(reenter if reentry_stage == "close"
                             else lambda: events.append("close")),
            save_manifest_fn=(reenter if reentry_stage == "save"
                              else lambda: events.append("save")),
        )

    assert "unverified nested mutation" not in events
    assert marker.path.is_file()
    assert not update.committed
