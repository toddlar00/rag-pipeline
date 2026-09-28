"""Real guard/recorder/observer composition with inert, distinct role sessions.

No model constructor, PDF reader, native recognition or model-file verification
runs here. Only the model verifier and underlying sessions are controlled; the
production wrappers and observation validation are exercised together.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import model_artifacts
from ocr_engine_guard import EngineAllocationLimit, RapidOCREngineGuard
from ocr_execution_receipt import ExecutionRecorder
from ocr_recovery_runtime import RapidOCRPageReader
from test_ocr_engine_guard import harness as harness


@pytest.fixture
def observed_reader(harness, monkeypatch, tmp_path):
    roles = {"detection": "text_det", "classification": "text_cls", "recognition": "text_rec"}
    verified_paths = {role: tmp_path / (role + ".onnx") for role in roles}
    models, sessions, originals = [], [], {}
    for index, (role, attribute) in enumerate(sorted(roles.items()), 1):
        digest = str(index) * 64
        models.append(SimpleNamespace(role=role, content_sha256=digest, size=100 + index))
        providers = ["CPUExecutionProvider"]
        options = SimpleNamespace(intra_op_num_threads=index, inter_op_num_threads=4 - index)
        inner = SimpleNamespace(get_providers=lambda p=providers: list(p),
                                get_session_options=lambda o=options: o)
        component = getattr(harness.raw, attribute)
        component.session.session = inner
        originals[role] = (component, component.session, inner)
        sessions.append({"role": role, "execution_providers": providers,
                         "thread_settings": {"intra_op": index, "inter_op": 4 - index},
                         "model_sha256": digest})
    monkeypatch.setattr(model_artifacts, "package_model", lambda _name: SimpleNamespace(version="3.9.2", files=models))
    verifications = []

    def verify(package, consumer):
        assert (package, consumer) == ("rapidocr", "docling_ocr")
        verifications.append(True)
        return dict(verified_paths)

    monkeypatch.setattr(model_artifacts, "verified_installed_package_model", verify)
    guard = RapidOCREngineGuard(harness.raw)

    class Reader(RapidOCRPageReader):
        def _load_engine(self):
            if self._engine is None:
                self._engine = guard
                self._engine_version = "3.9.2"
                self._verified_model_paths = dict(verified_paths)
            return self._engine

    recorder = ExecutionRecorder()
    reader = recorder.reader_factory(Reader)(Path("unused-generated-only.pdf"))
    try:
        yield SimpleNamespace(reader=reader, recorder=recorder, guard=guard, raw=harness.raw,
                              pixels=harness.pixels, np=harness.np, roles=roles, sessions=sessions,
                              originals=originals, verifications=verifications, verified_paths=verified_paths)
    finally:
        reader.close()


def _assert_observation(state):
    observed = state.reader.execution_observation()
    assert observed["engine"] == {"name": "rapidocr", "version": "3.9.2"}
    assert observed["sessions"] == state.sessions
    assert observed["model_artifacts"] == [
        {"id": role, "sha256": str(index) * 64, "bytes": 100 + index}
        for index, role in enumerate(sorted(state.roles), 1)]
    for role, attribute in state.roles.items():
        component, outer_session, inner_session = state.originals[role]
        assert getattr(state.raw, attribute) is component
        assert getattr(state.reader._engine, attribute) is component
        assert component.session is outer_session and component.session.session is inner_session
    return observed


def test_guard_preflight_is_not_an_ocr_call_but_constructed_sessions_remain_observable(observed_reader):
    state = observed_reader
    timed = state.reader._load_engine()
    before = _assert_observation(state)
    with pytest.raises(EngineAllocationLimit):
        timed.prepare(state.np.zeros((1, 6000, 3), dtype=state.np.uint8))
    assert state.recorder.calls == [] and state.raw.calls == 0
    assert _assert_observation(state) == before
    assert all(original[1].calls == [] for original in state.originals.values())
    state.reader.close()
    assert state.recorder.observation == before
    assert state.reader._engine is None and state.reader._engine_version is None
    assert state.reader._verified_model_paths is None
    assert len(state.verifications) == 3


@pytest.mark.parametrize("role", ["detection", "classification", "recognition"])
@pytest.mark.parametrize("error_type", [EngineAllocationLimit, ValueError, KeyboardInterrupt, SystemExit])
def test_failed_peer_restores_all_session_identities_and_keeps_exact_actual_call_accounting(
        observed_reader, role, error_type):
    state = observed_reader
    timed = state.reader._load_engine()
    before = _assert_observation(state)
    timed.prepare(state.pixels)
    assert state.recorder.calls == []
    assert timed(state.pixels).txts == ("ok",)
    assert _assert_observation(state) == before
    component, outer_session, _ = state.originals[role]
    failure = error_type("synthetic failure")
    outer_session.error = failure
    timed.prepare(state.pixels)
    with pytest.raises(error_type) as caught:
        timed(state.pixels)
    assert caught.value is failure
    assert _assert_observation(state) == before
    outer_session.error = None
    # A later independent peer reuses the original guarded engine after the
    # caller has observed the failure; the guard itself never silently retries.
    assert state.reader._load_engine() is timed
    timed.prepare(state.pixels)
    assert timed(state.pixels).txts == ("ok",)
    assert _assert_observation(state) == before
    assert component is state.originals[role][0]
    assert [(call["id"], call["status"]) for call in state.recorder.calls] == [
        ("call-0001", "completed"), ("call-0002", "failed"), ("call-0003", "completed")]
    assert state.raw.calls == 3
    assert all(call["elapsed_seconds"] >= 0 for call in state.recorder.calls)
    state.reader.close()
    assert state.recorder.observation == before and state.reader._engine is None
    assert len(state.verifications) == 5


def test_guard_observation_still_rejects_changed_approved_model_path_and_closes(observed_reader):
    state = observed_reader
    state.reader._load_engine()
    _assert_observation(state)
    state.verified_paths["recognition"] = state.verified_paths["recognition"].with_name("changed.onnx")
    with pytest.raises(ValueError, match="verified load paths"):
        state.reader.close()
    assert state.reader._engine is None and state.reader._verified_model_paths is None
    assert state.recorder.observation is None and state.recorder.calls == []
