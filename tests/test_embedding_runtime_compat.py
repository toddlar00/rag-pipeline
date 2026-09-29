"""The locked Transformers must keep the helper the pinned Nomic code calls.

The embedding model's pinned remote code (``modeling_hf_nomic_bert.py``, a
byte-verified runtime file in ``model-artifact-policy.json``) calls
``self.get_extended_attention_mask`` on every forward pass. Transformers
5.17.0 removed that ``PreTrainedModel`` helper: embedding, indexing and every
query then fail with "'NomicBertModel' object has no attribute
'get_extended_attention_mask'", while unit tests that never load the model
still pass. This guard runs only where Transformers is installed (the full
locked environment).
"""

import pytest


def test_transformers_keeps_the_extended_attention_mask_helper():
    transformers = pytest.importorskip("transformers")

    assert callable(getattr(transformers.PreTrainedModel,
                            "get_extended_attention_mask", None))
