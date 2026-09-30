"""The default local embedder must load the verified Nomic weights exactly.

The pinned Nomic remote ``from_pretrained`` builds every parameter as a real
CPU tensor, runs its random initializers, and then copies the verified
checkpoint in with ``load_state_dict(strict=False)``. ``_LocalEmbedFn``
skips those discarded initializers for that one verified model and then
proves, from the checkpoint header, that every parameter belongs to the
transformer and that the checkpoint declares each of the transformer's
state-dict tensors at its exact shape. That header proof cannot see a
non-persistent buffer or a temporary tensor initialized inside the skip
window.

The differential test is the oracle for that load path: it builds the model
the way the remote code does on its own (a direct ``SentenceTransformer`` load
of the verified bundle, random initialization included) and requires the
production load to reproduce every state-dict tensor, every buffer (including
the non-persistent ones the header proof cannot see) and the embeddings of
synthetic texts bit for bit. It runs in a child process, so the
loaded Torch, the process-private Transformers module cache and roughly 2 GB
of weights never leak into other tests, and it skips unless Torch, Sentence
Transformers and a verified cached bundle are all present. No CI workflow
syncs model bundles, so rerun it locally whenever torch, transformers,
sentence-transformers or the Nomic artifacts change in the locks.

The remaining tests are dependency-light: a fake ``torch.nn.init`` module,
synthetic safetensors headers and a fake Sentence Transformers class.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

import model_artifacts
import rag


ROOT = Path(__file__).resolve().parents[1]
NOMIC = "nomic-ai/nomic-embed-text-v2-moe"

_DIFFERENTIAL_PROBE = r'''
import contextlib
import gc
import hashlib
import json

import numpy as np
import torch

import model_artifacts
import rag

MODEL = rag.DEFAULT_EMBEDDING_MODEL_GENERAL
WORDS = ("court duty care breach contract property remedy statute notice "
         "hearing record appeal agency tenant easement").split()
TEXTS = [" ".join(WORDS[index:] + WORDS[:index]) for index in range(8)]
DOCUMENTS = rag._prepare_embedding_inputs(list(TEXTS), MODEL, "document")
QUERIES = rag._prepare_embedding_inputs(list(TEXTS), MODEL, "query")


def tensor_digest(tensor):
    flat = tensor.detach().contiguous().reshape(-1).view(torch.uint8)
    header = f"{tensor.dtype}:{tuple(tensor.shape)}:".encode("ascii")
    return hashlib.sha256(header + flat.numpy().tobytes()).hexdigest()


def vectors_digest(vectors):
    return hashlib.sha256(
        json.dumps(vectors, allow_nan=False).encode("ascii")).hexdigest()


def snapshot(model, documents):
    transformer = model[0].auto_model
    queries = np.asarray(model.encode(QUERIES, convert_to_numpy=True))
    return {
        "state_dict": {
            name: tensor_digest(tensor)
            for name, tensor in transformer.state_dict().items()},
        "buffers": {
            name: tensor_digest(tensor)
            for name, tensor in transformer.named_buffers()},
        "parameters": sum(parameter.numel()
                          for parameter in model.parameters()),
        "documents": vectors_digest(documents),
        "queries": vectors_digest(queries.tolist()),
    }


spec = model_artifacts.runtime_bundle_spec(MODEL, "embedding")
model_artifacts.configure_transformers_dynamic_module_cache()
from sentence_transformers import SentenceTransformer

baseline_model = SentenceTransformer(
    str(spec.target), trust_remote_code=True, local_files_only=True,
    model_kwargs={"use_safetensors": True})
baseline = snapshot(baseline_model, [
    vector.tolist() for vector in baseline_model.encode(
        DOCUMENTS, convert_to_numpy=True)])
del baseline_model
gc.collect()

skipped_calls = []
production_skip = rag._skipped_torch_init


@contextlib.contextmanager
def recording_skip():
    with production_skip() as skipped:
        yield skipped
    skipped_calls.append(skipped[0])


rag._skipped_torch_init = recording_skip
embedding = rag._get_embedding_fn(MODEL, input_type="document")
candidate_model = embedding._load()
candidate = snapshot(candidate_model, embedding(list(TEXTS)))
print(json.dumps({
    "baseline": baseline, "candidate": candidate,
    "skipped_calls": skipped_calls,
}))
'''


def _verified_nomic_bundle():
    for module in ("numpy", "torch", "sentence_transformers"):
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{module} is not installed")
    spec = model_artifacts.runtime_bundle_spec(
        rag.DEFAULT_EMBEDDING_MODEL_GENERAL, "embedding")
    try:
        present = model_artifacts.verify_cached_runtime_bundle(spec)
    except model_artifacts.ModelArtifactError as exc:
        pytest.skip(f"the cached Nomic embedding bundle does not verify: {exc}")
    if not present:
        pytest.skip("the verified Nomic embedding bundle is not cached")
    return spec


def test_production_nomic_load_matches_the_remote_code_load_bit_for_bit():
    _verified_nomic_bundle()
    environment = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    result = subprocess.run(
        [sys.executable, "-c", _DIFFERENTIAL_PROBE],
        capture_output=True, check=False, cwd=ROOT, env=environment,
        text=True, timeout=1200,
    )

    assert result.returncode == 0, result.stderr[-4000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    baseline, candidate = report["baseline"], report["candidate"]
    assert baseline["state_dict"]
    assert baseline["buffers"]
    assert candidate["state_dict"] == baseline["state_dict"]
    assert candidate["buffers"] == baseline["buffers"]
    assert candidate["parameters"] == baseline["parameters"]
    assert candidate["documents"] == baseline["documents"]
    assert candidate["queries"] == baseline["queries"]
    # The production load really took the skip path; a loader change that
    # made it a no-op would otherwise pass unnoticed.
    assert len(report["skipped_calls"]) == 1
    assert report["skipped_calls"][0] > 0


# --- The thread-scoped initializer skip -------------------------------------


class _Tensor:
    def __init__(self, shape=(2,)):
        self.shape = shape
        self.initialized_by = []


def _fake_torch_init(monkeypatch):
    module = ModuleType("torch.nn.init")

    def initializer(name):
        def initialize(tensor, *_args, **_kwargs):
            tensor.initialized_by.append(name)
            return tensor
        return initialize

    for name in rag._TORCH_INIT_FUNCTION_NAMES:
        setattr(module, name, initializer(name))
    monkeypatch.setitem(sys.modules, "torch.nn.init", module)
    return module


def _functions(module):
    return {name: getattr(module, name, None)
            for name in rag._TORCH_INIT_FUNCTION_NAMES}


def test_owner_thread_initializers_are_skipped_counted_and_restored(
        monkeypatch):
    init = _fake_torch_init(monkeypatch)
    del init.sparse_
    init.orthogonal_ = None
    originals = _functions(init)
    tensor = _Tensor()

    with rag._skipped_torch_init() as skipped:
        assert init.normal_ is not originals["normal_"]
        returned = init.normal_(tensor, std=0.02)
        init.zeros_(tensor=tensor)

    assert returned is tensor
    assert tensor.initialized_by == []
    assert skipped == [2]
    assert _functions(init) == originals
    assert not hasattr(init, "sparse_")


def test_other_threads_initialize_normally_inside_the_window(monkeypatch):
    init = _fake_torch_init(monkeypatch)
    owner_tensor, other_tensor = _Tensor(), _Tensor()

    with rag._skipped_torch_init() as skipped:
        worker = threading.Thread(target=init.normal_, args=(other_tensor,))
        worker.start()
        worker.join()
        init.normal_(owner_tensor)

    assert other_tensor.initialized_by == ["normal_"]
    assert owner_tensor.initialized_by == []
    assert skipped == [1]


def test_initializers_are_restored_when_the_load_raises(monkeypatch):
    init = _fake_torch_init(monkeypatch)
    originals = _functions(init)

    with pytest.raises(KeyError, match="load failed"):
        with rag._skipped_torch_init():
            raise KeyError("load failed")

    assert _functions(init) == originals


def test_a_captured_wrapper_passes_through_after_the_window(monkeypatch):
    init = _fake_torch_init(monkeypatch)
    tensor = _Tensor()

    with rag._skipped_torch_init() as skipped:
        captured = init.kaiming_uniform_

    assert captured(tensor, a=5 ** 0.5) is tensor
    assert tensor.initialized_by == ["kaiming_uniform_"]
    assert skipped == [0]


def test_a_later_patcher_is_never_clobbered(monkeypatch):
    init = _fake_torch_init(monkeypatch)
    originals = _functions(init)
    tensor = _Tensor()

    with rag._skipped_torch_init():
        replaced = init.normal_

        def guard(target, *args, **kwargs):
            target.initialized_by.append("guard")
            return replaced(target, *args, **kwargs)

        init.normal_ = guard

    assert init.normal_ is guard
    init.normal_(tensor)
    assert tensor.initialized_by == ["guard", "normal_"]
    assert {name: function for name, function in _functions(init).items()
            if name != "normal_"} == {
        name: function for name, function in originals.items()
        if name != "normal_"}


def _stack_depth():
    frame, depth = sys._getframe(1), 0
    while frame is not None:
        frame, depth = frame.f_back, depth + 1
    return depth


def test_interleaved_patchers_never_stack_leftover_wrappers(monkeypatch):
    init = _fake_torch_init(monkeypatch)
    depths = []

    def normal_(tensor, *_args, **_kwargs):
        depths.append(_stack_depth())
        tensor.initialized_by.append("normal_")
        return tensor

    def guarded_normal(tensor, *args, **kwargs):
        # Like Transformers' init guard: it calls Torch's own function, not
        # the slot value it saved.
        return normal_(tensor, *args, **kwargs)

    init.normal_ = normal_
    for _ in range(50):
        skipped_tensor, tensor = _Tensor(), _Tensor()
        with rag._skipped_torch_init() as skipped:
            init.normal_(skipped_tensor)
            # A patcher interleaving with the window (on another thread in
            # production) saves this window's wrapper as its original ...
            saved = init.normal_
            init.normal_ = guarded_normal
        # ... and reinstalls it after the window closed.
        init.normal_ = saved
        init.normal_(tensor)

        assert skipped == [1]
        assert skipped_tensor.initialized_by == []
        assert tensor.initialized_by == ["normal_"]

    # Each leftover replaced the previous one instead of wrapping it, so the
    # pass-through chain never deepened ...
    assert len(depths) == 50
    assert len(set(depths)) == 1
    # ... and the next undisturbed window restores Torch's own function.
    with rag._skipped_torch_init():
        pass
    assert init.normal_ is normal_


def test_nested_windows_on_one_thread_count_and_restore_in_order(
        monkeypatch):
    init = _fake_torch_init(monkeypatch)
    originals = _functions(init)

    with rag._skipped_torch_init() as outer:
        outer_wrappers = _functions(init)
        with rag._skipped_torch_init() as inner:
            init.ones_(_Tensor())
        assert _functions(init) == outer_wrappers
        init.ones_(_Tensor())

    assert (outer, inner) == ([1], [1])
    assert _functions(init) == originals


@pytest.mark.parametrize("absent", ["missing", "blocked"])
def test_nothing_is_patched_without_a_loaded_torch_init(monkeypatch, absent):
    if absent == "missing":
        monkeypatch.delitem(sys.modules, "torch.nn.init", raising=False)
    else:
        monkeypatch.setitem(sys.modules, "torch.nn.init", None)

    with rag._skipped_torch_init() as skipped:
        pass

    assert skipped == [0]


# --- The bounded safetensors checkpoint guard -------------------------------


def _checkpoint(path, header=None, *, raw=None, length=None):
    payload = (raw if raw is not None
               else json.dumps(header).encode("utf-8"))
    declared = len(payload) if length is None else length
    path.write_bytes(declared.to_bytes(8, "little") + payload + b"\0" * 16)
    return path


def _tensor_entry(shape):
    return {"dtype": "F32", "shape": list(shape), "data_offsets": [0, 0]}


def test_checkpoint_guard_accepts_every_declared_tensor(tmp_path):
    path = _checkpoint(tmp_path / "model.safetensors", {
        "__metadata__": {"format": "pt"},
        "encoder.weight": _tensor_entry((4, 3)),
        "encoder.bias": _tensor_entry((4,)),
        "scale": _tensor_entry(()),
        "unused.weight": _tensor_entry((1,)),
    })

    assert model_artifacts.safetensors_tensor_shapes(path) == {
        "encoder.weight": (4, 3),
        "encoder.bias": (4,),
        "scale": (),
        "unused.weight": (1,),
    }
    model_artifacts.require_checkpoint_tensors(
        path, {"encoder.weight": (4, 3), "encoder.bias": [4], "scale": ()},
        model_id="owner/model")


@pytest.mark.parametrize(("tensors", "message"), [
    ({"encoder.weight": (4, 3), "missing.weight": (2,)},
     r"does not supply 1 loaded tensor\(s\) .*: missing.weight$"),
    ({"encoder.weight": (3, 4)},
     r"does not supply 1 loaded tensor\(s\) .*: encoder.weight$"),
    ({f"missing.{index}": (1,) for index in range(7)},
     r"does not supply 7 loaded tensor\(s\) .*: "
     r"missing.0, missing.1, missing.2, missing.3, missing.4$"),
    ({}, "no loaded tensors to prove"),
])
def test_checkpoint_guard_fails_closed_on_uncovered_tensors(
        tmp_path, tensors, message):
    path = _checkpoint(tmp_path / "model.safetensors", {
        "encoder.weight": _tensor_entry((4, 3)),
    })

    with pytest.raises(model_artifacts.ModelArtifactError, match=message):
        model_artifacts.require_checkpoint_tensors(
            path, tensors, model_id="owner/model")


@pytest.mark.parametrize(("case", "message"), [
    ("short-prefix", "header length is invalid"),
    ("tiny-length", "header length is invalid"),
    ("oversize-length", "header length is invalid"),
    ("truncated", "header is truncated"),
    ("not-utf8", "not UTF-8 JSON"),
    ("not-json", "not UTF-8 JSON"),
    ("not-object", "not a JSON object"),
    ("duplicate", "repeats entry 'a'"),
    ("entry-not-object", "invalid shape for 'a'"),
    ("missing-shape", "invalid shape for 'a'"),
    ("negative-dimension", "invalid shape for 'a'"),
    ("boolean-dimension", "invalid shape for 'a'"),
    ("float-dimension", "invalid shape for 'a'"),
    ("shape-not-list", "invalid shape for 'a'"),
    ("absent-file", "could not read safetensors header"),
])
def test_checkpoint_header_reader_fails_closed(tmp_path, case, message):
    path = tmp_path / "model.safetensors"
    entry = _tensor_entry((2,))
    if case == "short-prefix":
        path.write_bytes(b"\x10\0\0")
    elif case == "tiny-length":
        _checkpoint(path, raw=b"{}", length=1)
    elif case == "oversize-length":
        _checkpoint(path, raw=b"{}", length=16 * 1024 * 1024 + 1)
    elif case == "truncated":
        path.write_bytes((100).to_bytes(8, "little") + b'{"a": {}}')
    elif case == "not-utf8":
        _checkpoint(path, raw=b'{"\xff": 1}')
    elif case == "not-json":
        _checkpoint(path, raw=b"{not json}")
    elif case == "not-object":
        _checkpoint(path, [entry])
    elif case == "duplicate":
        _checkpoint(path, raw=(
            b'{"a": {"shape": [2]}, "a": {"shape": [2]}}'))
    elif case == "entry-not-object":
        _checkpoint(path, {"a": [2]})
    elif case == "missing-shape":
        _checkpoint(path, {"a": {"dtype": "F32"}})
    elif case == "negative-dimension":
        _checkpoint(path, {"a": {"shape": [2, -1]}})
    elif case == "boolean-dimension":
        _checkpoint(path, {"a": {"shape": [True]}})
    elif case == "float-dimension":
        _checkpoint(path, {"a": {"shape": [2.0]}})
    elif case == "shape-not-list":
        _checkpoint(path, {"a": {"shape": 2}})

    with pytest.raises(model_artifacts.ModelArtifactError, match=message):
        model_artifacts.safetensors_tensor_shapes(path)


# --- The production loader --------------------------------------------------

_SHAPES = {"encoder.weight": (4, 3), "encoder.bias": (4,)}


class _FakeTransformer:
    def __init__(self, tensors):
        self._tensors = tensors

    def state_dict(self):
        return dict(self._tensors)

    def parameters(self):
        return iter(self._tensors.values())


class _FakeSentenceTransformer:
    """Allocate tensors, initialize them through torch.nn.init, like Nomic."""

    initialize = True
    extra_parameters = 0
    structured = True
    max_seq_length = None
    built = []

    def __init__(self, source, **kwargs):
        self.source, self.kwargs = source, kwargs
        self.tensors = {name: _Tensor(shape)
                        for name, shape in _SHAPES.items()}
        self.extra = [_Tensor() for _ in range(self.extra_parameters)]
        if self.initialize:
            init = sys.modules["torch.nn.init"]
            for tensor in [*self.tensors.values(), *self.extra]:
                init.normal_(tensor, std=0.02)
        self.transformer = _FakeTransformer(self.tensors)
        type(self).built.append(self)

    def __getitem__(self, index):
        if not self.structured:
            raise TypeError("not a module sequence")
        return [SimpleNamespace(auto_model=self.transformer)][index]

    def parameters(self):
        return iter([*self.tensors.values(), *self.extra])


def _fake_loader(monkeypatch, tmp_path, *, verified=True,
                 trust_remote_code=True, **behavior):
    init = _fake_torch_init(monkeypatch)
    monkeypatch.setattr(
        rag, "_model_loader_source",
        lambda model_id, *_args, **_kwargs: (
            (str(tmp_path), True) if verified else (model_id, False)))
    monkeypatch.setattr(
        rag._model_artifacts, "model_artifact",
        lambda *_args: SimpleNamespace(trust_remote_code=trust_remote_code))
    fake = type("FakeSentenceTransformer", (_FakeSentenceTransformer,),
                {**behavior, "built": []})
    sentence_transformers = ModuleType("sentence_transformers")
    sentence_transformers.SentenceTransformer = fake
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", sentence_transformers)
    return init, fake


def _nomic_checkpoint(tmp_path, shapes=_SHAPES):
    return _checkpoint(tmp_path / "model.safetensors", {
        name: _tensor_entry(shape) for name, shape in shapes.items()})


def test_verified_nomic_load_skips_initializers_after_proving_the_checkpoint(
        monkeypatch, tmp_path):
    init, fake = _fake_loader(monkeypatch, tmp_path)
    originals = _functions(init)
    _nomic_checkpoint(tmp_path)
    embedding = rag._get_embedding_fn(NOMIC)

    model = embedding._load()

    assert fake.built == [model]
    assert embedding._model is model
    assert [tensor.initialized_by for tensor in model.tensors.values()] == [
        [], []]
    assert model.source == str(tmp_path)
    assert model.kwargs == {
        "trust_remote_code": True,
        "local_files_only": True,
        "model_kwargs": {"use_safetensors": True},
    }
    assert _functions(init) == originals
    assert embedding._load() is model
    assert len(fake.built) == 1


@pytest.mark.parametrize(("behavior", "shapes", "message"), [
    ({}, {"encoder.weight": (4, 3)},
     r"does not supply 1 loaded tensor\(s\) .*: encoder.bias$"),
    ({}, {"encoder.weight": (4, 3), "encoder.bias": (3,)},
     r"does not supply 1 loaded tensor\(s\) .*: encoder.bias$"),
    ({}, None, "could not read safetensors header"),
    ({"extra_parameters": 1}, _SHAPES,
     r"has 1 parameter\(s\) outside its checkpointed transformer"),
    ({"structured": False}, _SHAPES,
     r"unexpected model structure \(TypeError\)"),
])
def test_unproven_nomic_load_fails_closed_and_is_never_cached(
        monkeypatch, tmp_path, behavior, shapes, message):
    _init, fake = _fake_loader(monkeypatch, tmp_path, **behavior)
    if shapes is not None:
        _nomic_checkpoint(tmp_path, shapes)
    embedding = rag._get_embedding_fn(NOMIC)

    for attempt in (1, 2):
        with pytest.raises(model_artifacts.ModelArtifactError, match=message):
            embedding._load()
        assert embedding._model is None
        assert len(fake.built) == attempt


@pytest.mark.parametrize(("model_name", "verified"), [
    (NOMIC, False),
    ("nlpaueb/legal-bert-base-uncased", True),
    ("dunzhang/stella_en_400M_v5", True),
])
def test_other_embedding_loads_keep_their_initializers(
        monkeypatch, tmp_path, model_name, verified):
    _init, fake = _fake_loader(monkeypatch, tmp_path, verified=verified)
    embedding = rag._get_embedding_fn(model_name)

    model = embedding._load()

    assert embedding._model is model
    assert [tensor.initialized_by for tensor in model.tensors.values()] == [
        ["normal_"], ["normal_"]]
    assert not (tmp_path / "model.safetensors").exists()


def test_nomic_load_that_skips_nothing_needs_no_checkpoint_proof(
        monkeypatch, tmp_path):
    _init, _fake = _fake_loader(monkeypatch, tmp_path, initialize=False)
    embedding = rag._get_embedding_fn(NOMIC)

    model = embedding._load()

    assert embedding._model is model
    assert not (tmp_path / "model.safetensors").exists()
