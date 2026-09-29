"""The locked Transformers must keep the names the pinned Nomic code uses.

The embedding model's pinned remote code (the byte-verified
``embedding_remote_code`` runtime files in ``model-artifacts.lock.json``)
imports names from Transformers and calls inherited ``PreTrainedModel``
helpers. Unit tests never load that model, so a Transformers release that
drops any of them passes them while every real embedding, indexing step and
query fails. That happened with 5.17.0, which removed
``PreTrainedModel.get_extended_attention_mask``.

``NOMIC_TRANSFORMERS_CONTRACT`` lists those names, derived from the pinned
files' syntax trees. It pins names only, not call signatures or output fields.
The contract guards run wherever Transformers is installed (the full locked
environment). A second check re-derives the contract from the pinned files
when they are in the local Hugging Face cache, so the committed list cannot
silently fall behind a model-artifact update; it skips elsewhere.
"""

import ast
import hashlib
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOMIC_CODE_MODEL = "nomic-ai/nomic-bert-2048"
NOMIC_CODE_CONSUMER = "embedding_remote_code"

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
# Self-attributes the files read without assigning, beyond torch.nn.Module
# class members, that do not come from Transformers: the instance attribute
# torch.nn.Module sets in __init__ (training), buffers the modeling file
# registers through register_buffer, and one rotary-embedding setting
# (scaling_factor) that it reads on a branch but never sets.
NOMIC_NON_TRANSFORMERS_SELF_ATTRIBUTES = [
    "inv_freq", "norm_factor", "scale", "scaling_factor", "training",
]
_RECEIVERS = {"self", "cls"}
# Name-taking attribute functions: True when the call assigns the attribute.
_ATTRIBUTE_FUNCTIONS = {"getattr": False, "hasattr": False, "setattr": True, "delattr": True}


def _pinned_nomic_code():
    lock = json.loads((ROOT / "model-artifacts.lock.json").read_text(encoding="utf-8"))
    model = next(item for item in lock["models"] if item["model_id"] == NOMIC_CODE_MODEL)
    digests = {item["path"]: item["content_sha256"] for item in model["files"]}
    return model["revision"], {path: digests[path]
                               for path in model["runtime_files"][NOMIC_CODE_CONSUMER]}


def _receiver_accesses(nodes):
    """Yield (attribute, assigned) for each self/cls attribute access."""
    for node in nodes:
        for child in ast.walk(node):
            if (isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
                    and child.value.id in _RECEIVERS):
                yield child.attr, isinstance(child.ctx, ast.Store)
            elif (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                    and child.func.id in _ATTRIBUTE_FUNCTIONS and child.args
                    and isinstance(child.args[0], ast.Name)
                    and child.args[0].id in _RECEIVERS):
                name = child.args[1] if len(child.args) > 1 else None
                literal = isinstance(name, ast.Constant) and isinstance(name.value, str)
                # A computed name can never match the committed contract.
                yield (name.value if literal else "<dynamic>",
                       _ATTRIBUTE_FUNCTIONS[child.func.id])


def _class_members(node):
    names = set()
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(item.name)
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _derive_contract(sources):
    imports, inherited = {}, set()
    for source in sources:
        tree = ast.parse(source)
        classes = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # A module import hides which names the code reads, so it can
                # never match the committed contract.
                for alias in node.names:
                    if alias.name.split(".")[0] == "transformers":
                        imports.setdefault(alias.name, set()).add("<module>")
            elif (isinstance(node, ast.ImportFrom) and node.module
                    and node.module.split(".")[0] == "transformers"):
                imports.setdefault(node.module, set()).update(
                    alias.name for alias in node.names)
            elif isinstance(node, ast.ClassDef):
                classes[node.name] = node
        # Each class may use what it or a same-file base class defines.
        own = {}
        for name, node in classes.items():
            defined, used = _class_members(node), set()
            for attribute, assigned in _receiver_accesses([node]):
                (defined if assigned else used).add(attribute)
            own[name] = defined, used

        def available(name, seen=frozenset()):
            names = set(own[name][0])
            for base in classes[name].bases:
                if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
                    names |= available(base.id, seen | {name})
            return names

        for name, (_, used) in own.items():
            inherited |= used - available(name)
        outside = list(_receiver_accesses(
            node for node in tree.body if not isinstance(node, ast.ClassDef)))
        inherited |= ({attribute for attribute, assigned in outside if not assigned}
                      - {attribute for attribute, assigned in outside if assigned})
    return {"imports": {module: sorted(names) for module, names in sorted(imports.items())},
            "inherited": sorted(inherited)}


def test_transformers_provides_the_names_the_pinned_nomic_code_imports():
    pytest.importorskip("transformers")

    missing = []
    for module, names in NOMIC_TRANSFORMERS_CONTRACT["imports"].items():
        try:
            imported = importlib.import_module(module)
        except ImportError:
            missing.append(module)
            continue
        missing.extend(f"{module}.{name}" for name in names if not hasattr(imported, name))

    assert missing == []


def test_transformers_keeps_the_pretrained_model_helpers_the_nomic_code_calls():
    transformers = pytest.importorskip("transformers")

    missing = [name for name in NOMIC_TRANSFORMERS_CONTRACT["pretrained_model_methods"]
               if not callable(getattr(transformers.PreTrainedModel, name, None))]

    assert missing == []


def test_the_contract_derivation_sees_indirect_transformers_use():
    source = (
        "import transformers\n"
        "class A(transformers.PreTrainedModel):\n"
        "    @classmethod\n"
        "    def build(cls):\n"
        "        return cls.from_pretrained('x')\n"
        "    def embeddings(self):\n"
        "        return getattr(self, 'get_input_embeddings')()\n"
        "    def named(self, name):\n"
        "        return getattr(self, name)\n"
        "class B(A):\n"
        "    helper = None\n"
        "    def run(self):\n"
        "        self.helper, self.build\n"
        "        return self.post_init()\n")

    assert _derive_contract([source]) == {
        "imports": {"transformers": ["<module>"]},
        "inherited": ["<dynamic>", "from_pretrained", "get_input_embeddings", "post_init"],
    }


def test_the_contract_matches_the_pinned_nomic_code_when_it_is_cached():
    torch = pytest.importorskip("torch")
    constants = pytest.importorskip("huggingface_hub.constants")
    revision, digests = _pinned_nomic_code()
    snapshot = (Path(constants.HF_HUB_CACHE)
                / ("models--" + NOMIC_CODE_MODEL.replace("/", "--")) / "snapshots" / revision)
    if not all((snapshot / path).is_file() for path in digests):
        pytest.skip("the pinned Nomic remote code is not in the local Hugging Face cache")
    sources = []
    for path, digest in sorted(digests.items()):
        data = (snapshot / path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, path
        sources.append(data.decode("utf-8"))

    derived = _derive_contract(sources)

    assert derived["imports"] == NOMIC_TRANSFORMERS_CONTRACT["imports"]
    inherited = [name for name in derived["inherited"] if not hasattr(torch.nn.Module, name)]
    assert inherited == sorted(
        NOMIC_TRANSFORMERS_CONTRACT["pretrained_model_methods"]
        + NOMIC_NON_TRANSFORMERS_SELF_ATTRIBUTES)
