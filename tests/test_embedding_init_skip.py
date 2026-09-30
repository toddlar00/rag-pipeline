"""The default local embedder must load the verified Nomic weights exactly.

The pinned Nomic remote ``from_pretrained`` builds every parameter as a real
CPU tensor, runs its random initializers, and then copies the verified
checkpoint in with ``load_state_dict(strict=False)``. The differential test
below is the oracle for any change to that load path: it builds the model the
way that remote code does on its own (a direct ``SentenceTransformer`` load
of the verified bundle, random initialization included) and requires the
production ``_LocalEmbedFn`` load to reproduce every state-dict tensor, every
buffer and the embeddings of synthetic texts bit for bit.

It runs in a child process, so the loaded Torch, the process-private
Transformers module cache and roughly 2 GB of weights never leak into other
tests, and it skips unless Torch, Sentence Transformers and a verified cached
bundle are all present. No CI workflow syncs model bundles, so rerun it
locally whenever torch, transformers, sentence-transformers or the Nomic
artifacts change in the locks.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import model_artifacts
import rag


ROOT = Path(__file__).resolve().parents[1]

_DIFFERENTIAL_PROBE = r'''
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

embedding = rag._get_embedding_fn(MODEL, input_type="document")
candidate_model = embedding._load()
candidate = snapshot(candidate_model, embedding(list(TEXTS)))
print(json.dumps({"baseline": baseline, "candidate": candidate}))
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
