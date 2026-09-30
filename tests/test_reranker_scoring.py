"""Single-pass local rerank scoring against the locked FlagEmbedding path.

``rag._rerank`` scores a local pool through ``reranker_scoring.score_pairs``.
For the locked ``FlagReranker`` it replays the library's scoring without the
discarded probe forward; everything else goes through ``compute_score``.
The library's own ``compute_score`` is the differential oracle.

The tests that need the real library build a tiny, randomly initialized
XLM-RoBERTa cross-encoder and a word-level fast tokenizer in a temporary
directory, so nothing is downloaded, and they are skipped where torch,
tokenizers, transformers or FlagEmbedding is absent.  All text is synthetic.
"""

import logging
import math
from pathlib import Path
import random
import subprocess
import sys
from types import ModuleType

import pytest

import process_supervision
import rag
import reranker_scoring


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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


def _bits(scores):
    assert type(scores) is list
    assert all(type(score) is float for score in scores)
    return [score.hex() for score in scores]


# --- Dependency-light behavior --------------------------------------------

class _LibraryOnly:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def compute_score(self, pairs, normalize):
        self.calls.append((pairs, normalize))
        return self.result


@pytest.fixture
def fake_library(monkeypatch):
    """A stand-in for the loaded encoder-only module at the locked version."""
    base = ModuleType(reranker_scoring._BASE_MODULE)

    class BaseReranker(_LibraryOnly):
        def __init__(self, result=None):
            super().__init__(result)
            self.target_devices = ["cpu"]

    base.BaseReranker = BaseReranker
    monkeypatch.setitem(sys.modules, reranker_scoring._BASE_MODULE, base)
    monkeypatch.setattr(
        reranker_scoring, "_installed_flagembedding_version",
        lambda: reranker_scoring.LOCKED_FLAGEMBEDDING_VERSION)
    return base


def test_reranker_scoring_is_a_standard_library_only_leaf():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; sys.path.insert(0, '.'); "
                "import reranker_scoring; "
                "forbidden = ('rag', 'numpy', 'torch', 'tqdm', 'transformers', "
                "'tokenizers', 'FlagEmbedding'); "
                "loaded = [name for name in sys.modules if any("
                "name == root or name.startswith(root + '.') "
                "for root in forbidden)]; "
                "assert not loaded, sorted(loaded)"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=PROJECT_ROOT,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_other_rerankers_score_through_compute_score_unchanged(monkeypatch):
    monkeypatch.setitem(sys.modules, "FlagEmbedding", ModuleType(
        "FlagEmbedding"))
    result = object()
    reranker = _LibraryOnly(result)
    pairs = [["query", "document"]]

    assert reranker_scoring._fast_path_base(reranker) is None
    assert reranker_scoring.score_pairs(reranker, pairs) is result
    assert reranker.calls == [(pairs, True)]
    assert reranker_scoring.score_pairs(
        reranker, pairs, normalize=False) is result
    assert reranker.calls[-1] == (pairs, False)


def test_only_the_exact_locked_single_device_class_qualifies(
        fake_library, monkeypatch):
    reranker = fake_library.BaseReranker()
    assert reranker_scoring._fast_path_base(reranker) is fake_library

    class Subclass(fake_library.BaseReranker):
        pass

    assert reranker_scoring._fast_path_base(Subclass()) is None

    for name in ("compute_score", "compute_score_single_gpu",
                 "get_detailed_inputs"):
        patched = fake_library.BaseReranker()
        setattr(patched, name, lambda *args, **kwargs: None)
        assert reranker_scoring._fast_path_base(patched) is None, name

    for devices in (["cpu", "cpu"], [], ("cpu",), None):
        reranker.target_devices = devices
        assert reranker_scoring._fast_path_base(reranker) is None, devices
    reranker.target_devices = ["cuda:0"]
    assert reranker_scoring._fast_path_base(reranker) is fake_library

    for version in ("1.4.3", "1.4.2.post1", "2.0.0", None):
        monkeypatch.setattr(
            reranker_scoring, "_installed_flagembedding_version",
            lambda version=version: version)
        assert reranker_scoring._fast_path_base(reranker) is None, version

    monkeypatch.delitem(sys.modules, reranker_scoring._BASE_MODULE)
    assert reranker_scoring._fast_path_base(reranker) is None


def test_installed_version_comes_from_distribution_metadata(monkeypatch):
    def missing(name):
        assert name == "FlagEmbedding"
        raise reranker_scoring.importlib.metadata.PackageNotFoundError(name)

    reranker_scoring._installed_flagembedding_version.cache_clear()
    try:
        monkeypatch.setattr(
            reranker_scoring.importlib.metadata, "version", missing)
        assert reranker_scoring._installed_flagembedding_version() is None
        monkeypatch.setattr(
            reranker_scoring.importlib.metadata, "version",
            lambda name: "1.4.2")
        # The first answer is cached for the life of the process.
        assert reranker_scoring._installed_flagembedding_version() is None
    finally:
        reranker_scoring._installed_flagembedding_version.cache_clear()


def test_qualifying_reranker_uses_the_single_pass_replay(
        fake_library, monkeypatch):
    reranker = fake_library.BaseReranker(result="library")
    calls = []

    def replay(base, target, pairs, normalize):
        calls.append((base, target, pairs, normalize))
        return "replay"

    monkeypatch.setattr(reranker_scoring, "_single_pass", replay)
    pairs = [["query", "document"]]

    assert reranker_scoring.score_pairs(reranker, pairs) == "replay"
    assert reranker_scoring.score_pairs(
        reranker, pairs, normalize=False) == "replay"
    assert calls == [(fake_library, reranker, pairs, True),
                     (fake_library, reranker, pairs, False)]
    assert reranker.calls == []


def test_replay_failure_falls_back_and_logs_only_the_exception_type(
        fake_library, monkeypatch, caplog):
    reranker = fake_library.BaseReranker(result="library")

    def fail(*_args):
        raise RuntimeError("private passage text")

    monkeypatch.setattr(reranker_scoring, "_single_pass", fail)
    pairs = [["private query text", "private passage text"]]
    caplog.set_level(logging.DEBUG, logger=reranker_scoring.__name__)

    assert reranker_scoring.score_pairs(reranker, pairs) == "library"
    assert reranker.calls == [(pairs, True)]
    assert [record.getMessage() for record in caplog.records] == [
        "Single-pass rerank scoring fell back to FlagEmbedding "
        "(RuntimeError)"]


@pytest.mark.parametrize("interrupt", [
    KeyboardInterrupt(),
    process_supervision._SupervisorSignal(15),
], ids=["keyboard-interrupt", "supervisor-signal"])
def test_replay_interrupts_propagate_without_a_library_retry(
        fake_library, monkeypatch, interrupt):
    reranker = fake_library.BaseReranker(result="library")

    def interrupted(*_args):
        raise interrupt

    monkeypatch.setattr(reranker_scoring, "_single_pass", interrupted)

    with pytest.raises(type(interrupt)) as raised:
        reranker_scoring.score_pairs(reranker, [["query", "document"]])
    assert raised.value is interrupt
    assert reranker.calls == []


def test_rag_rerank_scores_local_pairs_through_the_seam(monkeypatch):
    reranker = object()
    calls = []

    def score_pairs(target, pairs, **kwargs):
        calls.append((target, pairs, kwargs))
        return [0.2, 0.7]

    monkeypatch.setattr(
        rag, "_get_reranker", lambda _model, **_kwargs: reranker)
    monkeypatch.setattr(rag._reranker_scoring, "score_pairs", score_pairs)

    ranked = rag._rerank(
        "query", ["first", "second"], [{}, {}], [0.1, 0.2], 2,
        reranker_model="model-a")

    assert calls == [(
        reranker, [["query", "first"], ["query", "second"]],
        {"normalize": True})]
    assert ranked == (["second", "first"], [{}, {}], [0.7, 0.2])

    monkeypatch.setattr(
        rag._reranker_scoring, "score_pairs", lambda *_a, **_k: 0.5)
    assert rag._rerank(
        "query", ["only"], [{}], [0.1], 1, reranker_model="model-a",
    ) == (["only"], [{}], [0.5])


# --- Locked library ---------------------------------------------------------

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


def test_fast_path_is_engaged_for_the_locked_flagembedding(tiny_model_dir):
    assert reranker_scoring._installed_flagembedding_version() == (
        reranker_scoring.LOCKED_FLAGEMBEDDING_VERSION), (
        "FlagEmbedding changed: re-verify the single-pass replay against "
        "the new compute_score_single_gpu before moving the pin")
    reranker = _reranker(tiny_model_dir)

    assert reranker_scoring._fast_path_base(reranker) is sys.modules[
        reranker_scoring._BASE_MODULE]


@pytest.mark.parametrize("normalize", [True, False],
                         ids=["normalized", "raw"])
@pytest.mark.parametrize("batch_size,count", _POOL_SHAPES)
def test_single_pass_matches_the_library_bit_for_bit_with_one_less_forward(
        tiny_model_dir, batch_size, count, normalize):
    pairs = _pairs(count)
    single = _reranker(tiny_model_dir, batch_size=batch_size)
    library = _reranker(tiny_model_dir, batch_size=batch_size)

    # Each path's first call on a fresh reranker moves the model to its
    # device and dtype; both then run again on a reused reranker.
    fresh_single = reranker_scoring.score_pairs(
        single, pairs, normalize=normalize)
    fresh_library = library.compute_score(pairs, normalize=normalize)
    batches = math.ceil(count / batch_size)
    assert (single.forwards, library.forwards) == (batches, batches + 1)
    assert _bits(fresh_single) == _bits(fresh_library)

    reused_single = reranker_scoring.score_pairs(
        library, pairs, normalize=normalize)
    reused_library = single.compute_score(pairs, normalize=normalize)
    assert _bits(reused_single) == _bits(reused_library) == _bits(
        fresh_library)
    assert single.use_fp16 is library.use_fp16 is False


def test_single_pass_accepts_one_flat_pair_like_the_library(tiny_model_dir):
    pair = ["court held", "agency rule statute"]
    single = _reranker(tiny_model_dir)
    library = _reranker(tiny_model_dir)

    assert _bits(reranker_scoring.score_pairs(single, pair)) == _bits(
        library.compute_score(pair, normalize=True))
    assert (single.forwards, library.forwards) == (1, 2)


def test_single_pass_keeps_the_library_progress_bars(tiny_model_dir, capsys):
    small = _reranker(tiny_model_dir, batch_size=128)
    large = _reranker(tiny_model_dir, batch_size=4)
    capsys.readouterr()  # model loading reports its own progress

    reranker_scoring.score_pairs(small, _pairs(3))
    assert capsys.readouterr().err == ""

    reranker_scoring.score_pairs(large, _pairs(130))
    stderr = capsys.readouterr().err
    assert "pre tokenize" in stderr
    assert "Compute Scores" in stderr


def test_subclassed_and_patched_rerankers_keep_the_library_path(
        tiny_model_dir):
    from FlagEmbedding import FlagReranker

    class Subclass(FlagReranker):
        pass

    pairs = _pairs(20)
    expected = _bits(_reranker(tiny_model_dir).compute_score(
        pairs, normalize=True))
    subclassed = _reranker(tiny_model_dir, cls=Subclass)
    assert _bits(reranker_scoring.score_pairs(subclassed, pairs)) == expected
    assert subclassed.forwards == 2

    patched = _reranker(tiny_model_dir)
    library_compute_score = patched.compute_score
    observed = []

    def compute_score(sentence_pairs, **kwargs):
        observed.append(kwargs)
        return library_compute_score(sentence_pairs, **kwargs)

    patched.compute_score = compute_score
    assert _bits(reranker_scoring.score_pairs(patched, pairs)) == expected
    assert observed == [{"normalize": True}]
    assert patched.forwards == 2

    several = _reranker(tiny_model_dir)
    several.target_devices = ["cpu", "cpu"]
    assert reranker_scoring._fast_path_base(several) is None


def test_replay_failure_keeps_the_library_adaptive_batch_shrink(
        tiny_model_dir):
    # Batches over ten pairs fail as an out-of-memory error would.  The
    # replay's first batch fails, and the library's probe then shrinks the
    # batch size exactly as it does without the replay.
    def limited(reranker):
        def reject_large_batches(_module, _args, kwargs):
            if kwargs["input_ids"].shape[0] > 10:
                raise RuntimeError("synthetic allocation failure")

        reranker.model.register_forward_pre_hook(
            reject_large_batches, with_kwargs=True)
        return reranker

    pairs = _pairs(20)
    single = limited(_reranker(tiny_model_dir))
    library = limited(_reranker(tiny_model_dir))

    fallback = reranker_scoring.score_pairs(single, pairs)
    expected = library.compute_score(pairs, normalize=True)

    assert _bits(fallback) == _bits(expected)
    assert single.forwards == library.forwards + 1


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
    assert reranker.forwards == 1
