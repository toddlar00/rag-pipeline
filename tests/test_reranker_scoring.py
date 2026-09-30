"""Local reranker scoring through the locked FlagEmbedding encoder-only path.

``rag._rerank`` scores a local pool with a ``FlagReranker``.  The tests that
need the real library build a tiny, randomly initialized XLM-RoBERTa
cross-encoder and a word-level fast tokenizer in a temporary directory, so
nothing is downloaded, and they are skipped where torch, tokenizers,
transformers or FlagEmbedding is absent.  All text is synthetic.
"""

import math
import random

import pytest

import rag


_WORDS = (
    "court held plaintiff defendant agency rule statute section review "
    "standard notice comment procedure due process hearing evidence record "
    "finding fact law deference congress delegation remand appeal"
).split()
_POOL_SHAPES = [
    pytest.param(batch_size, pairs, id=f"bs{batch_size}-n{pairs}")
    for batch_size in (128, 4)
    for pairs in (1, 3, 20, 130)
]


def _sentence(rng, words):
    return " ".join(rng.choice(_WORDS) for _ in range(words))


def _pairs(count, *, seed=3):
    # Passages up to 700 words exceed max_length=512, so many pairs truncate
    # to the same length and exercise the library's length-sort tie order.
    rng = random.Random(seed)
    query = _sentence(rng, 6)
    return [[query, _sentence(rng, rng.randint(1, 700))]
            for _ in range(count)]


@pytest.fixture(scope="module")
def tiny_model_dir(tmp_path_factory):
    torch = pytest.importorskip("torch")
    tokenizers = pytest.importorskip("tokenizers")
    transformers = pytest.importorskip("transformers")
    pytest.importorskip("FlagEmbedding")

    vocab = {"<s>": 0, "<pad>": 1, "</s>": 2, "<unk>": 3}
    for word in _WORDS:
        vocab.setdefault(word, len(vocab))
    tokenizer = tokenizers.Tokenizer(
        tokenizers.models.WordLevel(vocab=vocab, unk_token="<unk>"))
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer.post_processor = tokenizers.processors.RobertaProcessing(
        ("</s>", 2), ("<s>", 0))
    path = tmp_path_factory.mktemp("tiny-reranker")
    transformers.PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, bos_token="<s>", eos_token="</s>",
        pad_token="<pad>", unk_token="<unk>", sep_token="</s>",
        cls_token="<s>",
    ).save_pretrained(path)
    torch.manual_seed(0)
    config = transformers.XLMRobertaConfig(
        vocab_size=len(vocab), hidden_size=32, num_hidden_layers=2,
        num_attention_heads=2, intermediate_size=64,
        max_position_embeddings=600, num_labels=1, pad_token_id=1)
    transformers.XLMRobertaForSequenceClassification(config).save_pretrained(
        path)
    return path


def _reranker(path, *, batch_size=128, cls=None):
    """A real FlagReranker built as rag builds one, with a forward counter."""
    if cls is None:
        from FlagEmbedding import FlagReranker as cls
    reranker = cls(
        str(path), use_fp16=True, batch_size=batch_size, devices="cpu")
    reranker.forwards = 0

    def count(_module, _args):
        reranker.forwards += 1

    reranker.model.register_forward_pre_hook(count)
    return reranker


@pytest.mark.parametrize("batch_size,count", _POOL_SHAPES)
def test_locked_library_discards_a_probe_forward_before_scoring(
        tiny_model_dir, batch_size, count):
    reranker = _reranker(tiny_model_dir, batch_size=batch_size)

    scores = reranker.compute_score(_pairs(count), normalize=True)

    assert reranker.forwards == math.ceil(count / batch_size) + 1
    assert type(scores) is list and len(scores) == count
    assert all(type(score) is float and 0.0 <= score <= 1.0
               for score in scores)


def test_locked_library_progress_bars_appear_only_from_one_full_batch(
        tiny_model_dir, capsys):
    small = _reranker(tiny_model_dir, batch_size=128)
    large = _reranker(tiny_model_dir, batch_size=4)
    capsys.readouterr()  # model loading reports its own progress

    small.compute_score(_pairs(3), normalize=True)
    assert capsys.readouterr().err == ""

    large.compute_score(_pairs(130), normalize=True)
    stderr = capsys.readouterr().err
    assert "pre tokenize" in stderr
    assert "Compute Scores" in stderr


def test_rag_rerank_orders_the_pool_by_the_library_scores(
        tiny_model_dir, monkeypatch):
    reranker = _reranker(tiny_model_dir)
    monkeypatch.setattr(
        rag, "_get_reranker", lambda _model, **_kwargs: reranker)
    rng = random.Random(5)
    documents = [_sentence(rng, rng.randint(5, 600)) for _ in range(20)]
    metadatas = [{"section_path": _sentence(rng, 3)} if index % 2 else {}
                 for index in range(20)]
    distances = [index / 100 for index in range(20)]

    ranked = rag._rerank(
        "standard review agency", documents, metadatas, distances, 5,
        reranker_model="model-a")

    oracle = _reranker(tiny_model_dir).compute_score(
        [["standard review agency", rag._reranker_document(document, meta)]
         for document, meta in zip(documents, metadatas)],
        normalize=True)
    order = sorted(range(20), key=lambda index: oracle[index],
                   reverse=True)[:5]
    assert ranked == (
        [documents[index] for index in order],
        [metadatas[index] for index in order],
        [oracle[index] for index in order],
    )
