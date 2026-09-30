"""Single-pass scoring for the locked FlagEmbedding encoder-only reranker.

FlagEmbedding 1.4.2's ``BaseReranker.compute_score_single_gpu`` runs a full
forward pass over the first batch only to probe whether that batch size fits,
discards the result, and then scores every batch again.  A local rerank pool
usually fits in one batch, so each rerank ran the cross-encoder twice.

``score_pairs`` replays the library's single-device scoring step for step and
omits only that probe; the probed batch is exactly the first scored batch.
The replay runs only for an exact ``BaseReranker`` (``FlagReranker``) of the
pinned library version on one target device.  Every other reranker, and any
``Exception`` raised by the replay, goes through the library's own
``compute_score``, so its scores, adaptive batch shrink and errors are
unchanged.

Only the standard library is imported at module scope.  NumPy, PyTorch, tqdm
and FlagEmbedding's tokenizer helpers are imported inside the replay, which
runs only for a reranker the library has already loaded.
"""

from __future__ import annotations

import functools
import importlib.metadata
import logging
import sys
from types import ModuleType
from typing import Any


log = logging.getLogger(__name__)

# The replay mirrors abc/inference/AbsReranker.py and
# inference/reranker/encoder_only/base.py of exactly this release
# (requirements-core.lock).  Any other installed release takes the library
# path until the replay is re-verified against it by
# tests/test_reranker_scoring.py.
LOCKED_FLAGEMBEDDING_VERSION = "1.4.2"
_BASE_MODULE = "FlagEmbedding.inference.reranker.encoder_only.base"
_LIBRARY_ENTRY_POINTS = frozenset({
    "compute_score", "compute_score_single_gpu", "get_detailed_inputs"})

__all__ = ["LOCKED_FLAGEMBEDDING_VERSION", "score_pairs"]


@functools.lru_cache(maxsize=None)
def _installed_flagembedding_version() -> str | None:
    try:
        return importlib.metadata.version("FlagEmbedding")
    except importlib.metadata.PackageNotFoundError:
        return None


def _fast_path_base(reranker: object) -> ModuleType | None:
    """Return the library module the replay mirrors, or ``None``.

    FlagEmbedding is never imported here: only a reranker whose class is the
    already-loaded library class can qualify.  A subclass or an instance
    that overrides a library entry point may score differently, and several
    target devices use the library's multi-process pool.
    """
    base = sys.modules.get(_BASE_MODULE)
    if base is None or type(reranker) is not getattr(
            base, "BaseReranker", None):
        return None
    if _LIBRARY_ENTRY_POINTS.intersection(vars(reranker)):
        return None
    if _installed_flagembedding_version() != LOCKED_FLAGEMBEDDING_VERSION:
        return None
    devices = getattr(reranker, "target_devices", None)
    if not isinstance(devices, list) or len(devices) != 1:
        return None
    return base


def _single_pass(
        base: ModuleType, reranker: Any, sentence_pairs: Any,
        normalize: bool | None) -> list[float]:
    import numpy as np
    import torch
    from FlagEmbedding.utils.tokenizer_compat import (
        pad_with_compat, prepare_for_model_compat)
    from tqdm import tqdm, trange

    # AbsReranker.compute_score, single-device branch.
    if isinstance(sentence_pairs[0], str):
        sentence_pairs = [sentence_pairs]
    sentence_pairs = reranker.get_detailed_inputs(sentence_pairs)
    device = reranker.target_devices[0]

    # BaseReranker.compute_score_single_gpu, decorated @torch.no_grad().
    with torch.no_grad():
        batch_size = reranker.batch_size
        max_length = reranker.max_length
        if reranker.query_max_length is not None:
            query_max_length = reranker.query_max_length
        else:
            query_max_length = max_length * 3 // 4
        if normalize is None:
            normalize = reranker.normalize

        if device == "cpu":
            reranker.use_fp16 = False
        if reranker.use_fp16:
            reranker.model.half()

        reranker.model.to(device)
        reranker.model.eval()

        assert isinstance(sentence_pairs, list)
        if isinstance(sentence_pairs[0], str):
            sentence_pairs = [sentence_pairs]

        # Tokenize without padding to get each pair's true length.
        all_inputs = []
        for start_index in trange(0, len(sentence_pairs), batch_size,
                                  desc="pre tokenize",
                                  disable=len(sentence_pairs) < batch_size):
            sentences_batch = sentence_pairs[
                start_index:start_index + batch_size]
            queries = [s[0] for s in sentences_batch]
            passages = [s[1] for s in sentences_batch]
            queries_inputs_batch = reranker.tokenizer(
                queries,
                return_tensors=None,
                add_special_tokens=False,
                max_length=query_max_length,
                truncation=True,
            )["input_ids"]
            passages_inputs_batch = reranker.tokenizer(
                passages,
                return_tensors=None,
                add_special_tokens=False,
                max_length=max_length,
                truncation=True,
            )["input_ids"]
            for q_inp, d_inp in zip(
                    queries_inputs_batch, passages_inputs_batch):
                item = prepare_for_model_compat(
                    reranker.tokenizer,
                    q_inp,
                    d_inp,
                    truncation="only_second",
                    max_length=max_length,
                    padding=False,
                )
                all_inputs.append(item)
        # The same unstable argsort keeps tied lengths in the library's order,
        # so every batch holds the same pairs.
        length_sorted_idx = np.argsort(
            [-len(x["input_ids"]) for x in all_inputs])
        all_inputs_sorted = [all_inputs[i] for i in length_sorted_idx]

        # The library's discarded probe forward over
        # all_inputs_sorted[:batch_size] belongs here; it is omitted.
        all_scores = []
        for start_index in tqdm(range(0, len(all_inputs_sorted), batch_size),
                                desc="Compute Scores",
                                disable=len(all_inputs_sorted) < batch_size):
            sentences_batch = all_inputs_sorted[
                start_index:start_index + batch_size]
            inputs = pad_with_compat(
                reranker.tokenizer,
                sentences_batch,
                padding=True,
                return_tensors="pt",
            ).to(device)

            scores = reranker.model(
                **inputs, return_dict=True).logits.view(-1, ).float()
            all_scores.extend(scores.cpu().numpy().tolist())

        all_scores = [all_scores[idx] for idx in np.argsort(length_sorted_idx)]

        if normalize:
            all_scores = [base.sigmoid(score) for score in all_scores]

        return all_scores


def score_pairs(reranker: Any, pairs: Any, *, normalize: bool = True) -> Any:
    """Return ``reranker.compute_score(pairs, normalize=normalize)``.

    A qualifying FlagEmbedding reranker is scored in one pass with results
    identical to the library's.  Any other reranker, or any ``Exception``
    from the replay, uses ``compute_score`` itself and returns its result
    unchanged.  The fallback logs only the exception type: pairs hold corpus
    text.
    """
    base = _fast_path_base(reranker)
    if base is not None:
        try:
            return _single_pass(base, reranker, pairs, normalize)
        except Exception as exc:
            log.debug(
                "Single-pass rerank scoring fell back to FlagEmbedding (%s)",
                type(exc).__name__)
    return reranker.compute_score(pairs, normalize=normalize)
