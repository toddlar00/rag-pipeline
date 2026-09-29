"""The locked Transformers must keep the API the pinned Nomic code uses.

The embedding model's pinned remote code (``modeling_hf_nomic_bert.py``, a
byte-verified runtime file in ``model-artifacts.lock.json``) imports names
from Transformers and calls inherited ``PreTrainedModel`` helpers. Unit tests
never load that model, so a Transformers release that drops any of them passes
them while every real embedding, indexing step and query fails. That happened
with 5.17.0, which removed ``PreTrainedModel.get_extended_attention_mask``.

``NOMIC_TRANSFORMERS_CONTRACT`` is that API, derived from the pinned file's
syntax tree. The contract guards run wherever Transformers is installed (the
full locked environment). A second check re-derives the contract from the
pinned file when it is in the local Hugging Face cache, so the committed list
cannot silently fall behind a model-artifact update; it skips elsewhere.
"""

import ast
import hashlib
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOMIC_CODE_MODEL = "nomic-ai/nomic-bert-2048"
NOMIC_CODE_FILE = "modeling_hf_nomic_bert.py"

NOMIC_TRANSFORMERS_CONTRACT = {
    "imports": {
        "transformers": ["GPT2Config", "PreTrainedModel", "ViTConfig", "ViTModel"],
        "transformers.modeling_outputs": [
            "BaseModelOutput", "BaseModelOutputWithPast", "BaseModelOutputWithPooling",
            "MaskedLMOutput", "ModelOutput", "MultipleChoiceModelOutput",
            "QuestionAnsweringModelOutput", "SequenceClassifierOutput",
            "TokenClassifierOutput",
        ],
        "transformers.models.bert.modeling_bert": [
            "BaseModelOutputWithPoolingAndCrossAttentions", "MaskedLMOutput",
            "SequenceClassifierOutput",
        ],
        "transformers.utils": [
            "SAFE_WEIGHTS_INDEX_NAME", "SAFE_WEIGHTS_NAME", "WEIGHTS_INDEX_NAME",
            "WEIGHTS_NAME",
        ],
        "transformers.utils.hub": ["cached_file", "get_checkpoint_shard_files"],
    },
    "pretrained_model_methods": ["get_extended_attention_mask", "post_init"],
}
# Self-attributes the file reads without assigning, beyond torch.nn.Module class
# members, that do not come from Transformers: the instance attribute
# torch.nn.Module sets in __init__ (training), buffers the file registers
# through register_buffer, and rotary-embedding settings it sets indirectly.
NOMIC_NON_TRANSFORMERS_SELF_ATTRIBUTES = [
    "inv_freq", "norm_factor", "scale", "scaling_factor", "training",
]


def _pinned_nomic_code():
    lock = json.loads((ROOT / "model-artifacts.lock.json").read_text(encoding="utf-8"))
    model = next(item for item in lock["models"] if item["model_id"] == NOMIC_CODE_MODEL)
    entry = next(item for item in model["files"] if item["path"] == NOMIC_CODE_FILE)
    return model["revision"], entry["content_sha256"]


def _derive_contract(source):
    tree = ast.parse(source)
    imports = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom) and node.module
                and node.module.split(".")[0] == "transformers"):
            imports.setdefault(node.module, set()).update(alias.name for alias in node.names)
    defined, used = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            defined.update(item.name for item in node.body
                           if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)))
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            (defined if isinstance(node.ctx, ast.Store) else used).add(node.attr)
    return {"imports": {module: sorted(names) for module, names in sorted(imports.items())},
            "inherited": sorted(used - defined)}


def test_transformers_provides_the_names_the_pinned_nomic_code_imports():
    pytest.importorskip("transformers")

    missing = [f"{module}.{name}"
               for module, names in NOMIC_TRANSFORMERS_CONTRACT["imports"].items()
               for name in names
               if not hasattr(importlib.import_module(module), name)]

    assert missing == []


def test_transformers_keeps_the_pretrained_model_helpers_the_nomic_code_calls():
    transformers = pytest.importorskip("transformers")

    missing = [name for name in NOMIC_TRANSFORMERS_CONTRACT["pretrained_model_methods"]
               if not callable(getattr(transformers.PreTrainedModel, name, None))]

    assert missing == []


def test_the_contract_matches_the_pinned_nomic_code_when_it_is_cached():
    torch = pytest.importorskip("torch")
    constants = pytest.importorskip("huggingface_hub.constants")
    revision, digest = _pinned_nomic_code()
    path = (Path(constants.HF_HUB_CACHE) / ("models--" + NOMIC_CODE_MODEL.replace("/", "--"))
            / "snapshots" / revision / NOMIC_CODE_FILE)
    if not path.is_file():
        pytest.skip("the pinned Nomic remote code is not in the local Hugging Face cache")
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest

    derived = _derive_contract(data.decode("utf-8"))

    assert derived["imports"] == NOMIC_TRANSFORMERS_CONTRACT["imports"]
    inherited = [name for name in derived["inherited"] if not hasattr(torch.nn.Module, name)]
    assert inherited == sorted(
        NOMIC_TRANSFORMERS_CONTRACT["pretrained_model_methods"]
        + NOMIC_NON_TRANSFORMERS_SELF_ATTRIBUTES)
