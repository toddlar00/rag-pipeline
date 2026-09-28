"""The locked FlagEmbedding must support the locked Transformers major version.

FlagEmbedding 1.4.0 scores rerank pairs through
``tokenizer.prepare_for_model``, which Transformers 5 tokenizers no longer
provide. With that combination `rag.py query --rerank` logs "Reranker failed
(... has no attribute prepare_for_model)", keeps the hybrid ranking and still
exits 0. FlagEmbedding 1.4.2 routes those calls through
``FlagEmbedding.utils.tokenizer_compat``. This guard runs only where both
packages are installed (the full locked environment).
"""

import importlib
import importlib.metadata

import pytest


def _major(package):
    return int(importlib.metadata.version(package).split(".")[0])


def test_flagembedding_supports_the_installed_transformers_major():
    pytest.importorskip("FlagEmbedding")
    pytest.importorskip("transformers")
    if _major("transformers") < 5:
        pytest.skip("Transformers 4 tokenizers still provide prepare_for_model")

    compat = importlib.import_module("FlagEmbedding.utils.tokenizer_compat")

    assert callable(compat.prepare_for_model_compat)
    assert callable(compat.pad_with_compat)
