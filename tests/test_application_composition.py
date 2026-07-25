from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import importlib.util
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

import application_composition


_MISSING = object()


def _default_runtime_factory(_corpora, **_kwargs):
    return object()


def _default_http_factory(_runtime, _credentials, **_kwargs):
    return object()


def _composition(
        *, runtime_factory=_MISSING, http_factory=_MISSING, **changes):
    if runtime_factory is _MISSING:
        runtime_factory = _default_runtime_factory
    if http_factory is _MISSING:
        http_factory = _default_http_factory
    values = {
        "runtime_factory": runtime_factory,
        "http_factory": http_factory,
        "runtime_binding": object(),
        "job_coordination_binding": object(),
        "http_binding": object(),
    }
    values.update(changes)
    return application_composition.ServiceApplicationComposition(**values)


def test_composition_is_frozen_slotted_and_validates_every_field():
    composition = _composition()

    assert not hasattr(composition, "__dict__")
    with pytest.raises(FrozenInstanceError):
        composition.runtime_binding = object()

    for field in ("runtime_factory", "http_factory"):
        with pytest.raises(TypeError, match=field):
            _composition(**{field: None})
    for field in (
            "runtime_binding", "job_coordination_binding", "http_binding"):
        with pytest.raises(TypeError, match=field):
            _composition(**{field: None})


def test_runtime_factory_forwards_exact_defaults_and_omits_private_sentinels():
    captured = {}
    runtime = object()

    def runtime_factory(corpora, **kwargs):
        captured.update(corpora=corpora, kwargs=kwargs)
        return runtime

    composition = _composition(runtime_factory=runtime_factory)
    corpora = {"book": object()}

    result = application_composition.create_service_runtime(
        corpora, composition=composition)

    assert result is runtime
    assert captured == {
        "corpora": corpora,
        "kwargs": {
            "job_root": None,
            "working_directory": None,
            "output_root": None,
            "service_state_root": None,
            "ready_timeout_seconds": 10.0,
            "max_concurrent_searches": 2,
            "security_policy": None,
            "runtime_binding": composition.runtime_binding,
            "job_coordination_binding": (
                composition.job_coordination_binding),
        },
    }
    assert "search_runner" not in captured["kwargs"]
    assert "launcher" not in captured["kwargs"]


def test_runtime_factory_preserves_explicit_overrides_and_none_sentinels():
    captured = {}
    runtime_override = object()
    job_override = object()

    def runtime_factory(_corpora, **kwargs):
        captured.update(kwargs)
        return object()

    composition = _composition(runtime_factory=runtime_factory)

    application_composition.create_service_runtime(
        {},
        composition=composition,
        runtime_binding=runtime_override,
        job_coordination_binding=job_override,
        search_runner=None,
        launcher=None,
    )

    assert captured["runtime_binding"] is runtime_override
    assert captured["job_coordination_binding"] is job_override
    assert "search_runner" in captured and captured["search_runner"] is None
    assert "launcher" in captured and captured["launcher"] is None


def test_http_factory_receives_exact_binding_and_options():
    captured = {}
    app = object()
    runtime = object()
    credentials = object()

    def http_factory(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return app

    composition = _composition(http_factory=http_factory)

    result = application_composition.create_service_http_app(
        runtime,
        credentials,
        composition=composition,
        host="::1",
        reconcile_interval_seconds=3.5,
        max_http_concurrency=17,
        enforce_peer_loopback=False,
    )

    assert result is app
    assert captured == {
        "args": (runtime, credentials),
        "kwargs": {
            "http_binding": composition.http_binding,
            "host": "::1",
            "reconcile_interval_seconds": 3.5,
            "max_http_concurrency": 17,
            "enforce_peer_loopback": False,
        },
    }


def test_combined_factory_uses_one_snapshot_in_runtime_then_http_order(
        monkeypatch):
    events = []
    runtime = object()
    credentials = object()
    app = SimpleNamespace(state=SimpleNamespace(runtime=runtime))

    def runtime_factory(corpora, **kwargs):
        events.append(("runtime", corpora, kwargs))
        return runtime

    def http_factory(selected_runtime, selected_credentials, **kwargs):
        events.append((
            "http", selected_runtime, selected_credentials, kwargs))
        return app

    first = _composition(
        runtime_factory=runtime_factory, http_factory=http_factory)
    second = replace(
        first,
        runtime_factory=lambda *_args, **_kwargs: pytest.fail(
            "a later composition generation was mixed in"),
    )
    resolutions = []

    def default_composition():
        selected = first if not resolutions else second
        resolutions.append(selected)
        return selected

    monkeypatch.setattr(
        application_composition,
        "default_service_application_composition",
        default_composition,
    )

    result = application_composition.create_service_application(
        {"book": object()},
        credentials,
        job_root="jobs",
        host="::1",
    )

    assert result is app
    assert result.state.runtime is runtime
    assert resolutions == [first]
    assert [event[0] for event in events] == ["runtime", "http"]
    assert events[0][2]["runtime_binding"] is first.runtime_binding
    assert events[0][2]["job_coordination_binding"] is (
        first.job_coordination_binding)
    assert events[1][1] is runtime
    assert events[1][2] is credentials
    assert events[1][3]["http_binding"] is first.http_binding
    assert events[1][3]["host"] == "::1"


def test_combined_factory_preserves_omission_and_explicit_none():
    calls = []

    def runtime_factory(_corpora, **kwargs):
        calls.append(kwargs)
        return object()

    def http_factory(runtime, _credentials, **_kwargs):
        return SimpleNamespace(state=SimpleNamespace(runtime=runtime))

    composition = _composition(
        runtime_factory=runtime_factory, http_factory=http_factory)

    application_composition.create_service_application(
        {}, object(), composition=composition)
    application_composition.create_service_application(
        {}, object(), composition=composition,
        search_runner=None, launcher=None)

    assert "search_runner" not in calls[0]
    assert "launcher" not in calls[0]
    assert calls[1]["search_runner"] is None
    assert calls[1]["launcher"] is None


def test_importing_root_does_not_load_service_or_heavy_profiles():
    project_root = Path(__file__).resolve().parents[1]
    forbidden = (
        "fastapi", "starlette", "service_http", "service_runtime",
        "service_api", "service_runtime_binding", "job_coordination",
        "runtime_supervision", "embedding_policy", "resource_lease",
        "release_security", "uvicorn", "rag", "job_manager", "qdrant",
        "qdrant_client", "chromadb", "torch", "transformers", "docling",
    )
    source = (
        "import sys; "
        f"sys.path.insert(0, {str(project_root)!r}); "
        "import application_composition; "
        f"forbidden = {forbidden!r}; "
        "assert not [name for name in sys.modules "
        "if any(name == root or name.startswith(root + '.') "
        "for root in forbidden)]"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", source],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_default_service_composition_wires_exact_production_identities():
    if importlib.util.find_spec("fastapi") is None:
        pytest.skip("service HTTP dependencies are not installed")

    import job_coordination
    import service_http
    import service_runtime
    import service_runtime_binding

    composition = (
        application_composition._build_default_service_application_composition()
    )

    assert composition.runtime_factory is (
        service_runtime.RagApplicationService)
    assert composition.http_factory is service_http.create_app
    assert composition.runtime_binding is (
        service_runtime_binding.default_service_runtime_binding())
    assert composition.job_coordination_binding is (
        job_coordination.default_service_job_coordination_binding())
    assert composition.http_binding.runtime_error_type is (
        service_runtime.ServiceRuntimeError)
    assert composition.http_binding.default_job_page_limit == (
        service_runtime.DEFAULT_JOB_PAGE_LIMIT)
    assert composition.http_binding.max_job_page_limit == (
        service_runtime.MAX_JOB_PAGE_LIMIT)


def test_default_service_composition_cold_build_is_thread_safe_exact_singleton(
        monkeypatch):
    composition = _composition()
    entered = threading.Event()
    release = threading.Event()
    build_count = 0

    def build():
        nonlocal build_count
        build_count += 1
        entered.set()
        assert release.wait(timeout=30)
        return composition

    monkeypatch.setattr(
        application_composition,
        "_DEFAULT_SERVICE_APPLICATION_COMPOSITION",
        None,
    )
    monkeypatch.setattr(
        application_composition,
        "_build_default_service_application_composition",
        build,
    )
    observed = []
    threads = [threading.Thread(
        target=lambda: observed.append(
            application_composition.default_service_application_composition())
    ) for _ in range(8)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=30)
    release.set()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads)
    assert build_count == 1
    assert len(observed) == 8
    assert all(item is composition for item in observed)


@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_combined_factory_propagates_failures_without_partial_continuation(
        failure_type):
    runtime_failure = failure_type("runtime marker")
    http_failure = failure_type("http marker")
    events = []

    def fail_runtime(*_args, **_kwargs):
        events.append("runtime")
        raise runtime_failure

    def unexpected_http(*_args, **_kwargs):
        events.append("http")
        pytest.fail("HTTP construction must not follow runtime failure")

    with pytest.raises(failure_type) as raised:
        application_composition.create_service_application(
            {}, object(), composition=_composition(
                runtime_factory=fail_runtime,
                http_factory=unexpected_http,
            ))
    assert raised.value is runtime_failure
    assert events == ["runtime"]

    events.clear()

    def create_runtime(*_args, **_kwargs):
        events.append("runtime")
        return object()

    def fail_http(*_args, **_kwargs):
        events.append("http")
        raise http_failure

    with pytest.raises(failure_type) as raised:
        application_composition.create_service_application(
            {}, object(), composition=_composition(
                runtime_factory=create_runtime,
                http_factory=fail_http,
            ))
    assert raised.value is http_failure
    assert events == ["runtime", "http"]


def test_failed_default_build_is_not_cached_and_next_call_retries(
        monkeypatch):
    expected = _composition()
    marker = RuntimeError("build marker")
    calls = 0

    def build():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise marker
        return expected

    monkeypatch.setattr(
        application_composition,
        "_DEFAULT_SERVICE_APPLICATION_COMPOSITION",
        None,
    )
    monkeypatch.setattr(
        application_composition,
        "_build_default_service_application_composition",
        build,
    )

    with pytest.raises(RuntimeError) as raised:
        application_composition.default_service_application_composition()
    assert raised.value is marker
    assert application_composition._DEFAULT_SERVICE_APPLICATION_COMPOSITION is (
        None)
    assert (
        application_composition.default_service_application_composition()
        is expected)
    assert calls == 2


def test_explicit_composition_and_http_binding_never_consult_defaults(
        monkeypatch):
    observed = {}
    explicit_http_binding = object()

    def http_factory(*_args, **kwargs):
        observed.update(kwargs)
        return object()

    composition = _composition(http_factory=http_factory)
    monkeypatch.setattr(
        application_composition,
        "default_service_application_composition",
        lambda: pytest.fail("explicit composition must win"),
    )

    application_composition.create_service_http_app(
        object(), object(), composition=composition,
        http_binding=explicit_http_binding)

    assert observed["http_binding"] is explicit_http_binding


@pytest.mark.parametrize("factory,args", [
    (application_composition.create_service_runtime, ({},)),
    (application_composition.create_service_http_app, (object(), object())),
    (application_composition.create_service_application, ({}, object())),
])
def test_public_factories_reject_non_composition_objects(factory, args):
    with pytest.raises(TypeError, match="ServiceApplicationComposition"):
        factory(*args, composition=object())


def test_real_service_factories_bridge_current_concrete_signatures(tmp_path):
    if importlib.util.find_spec("fastapi") is None:
        pytest.skip("service HTTP dependencies are not installed")

    import service_contracts
    import service_http
    import service_runtime

    database_path = tmp_path / "qdrant"
    database_path.mkdir()
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text("", encoding="utf-8")
    config = service_contracts.CorpusConfig(
        corpus_id="property",
        db_path=database_path.resolve(),
        chunks_path=chunks_path.resolve(),
        collection_name="property",
        embedding_model="test-model",
        reindex_timeout_seconds=30,
    )
    composition = (
        application_composition._build_default_service_application_composition()
    )

    app = application_composition.create_service_application(
        {"property": config},
        service_http.ServiceCredentials("r" * 48, "a" * 48),
        composition=composition,
        working_directory=tmp_path,
        output_root=tmp_path / "output",
        job_root=tmp_path / "jobs",
        service_state_root=tmp_path / "state",
        launcher=None,
    )

    assert isinstance(app.state.runtime, service_runtime.RagApplicationService)
    assert app.state.runtime.corpora == {"property": config}
