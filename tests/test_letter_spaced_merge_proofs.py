"""Letter runs merge into content-stream words only under every proof.

``rag._content_stream_letter_words`` rejoins a run of same-line single ASCII
letters only when (a) exactly one content-stream word covers its first
letter, (b) that word spells exactly its letters, (c) every gap inside the
run is narrower than every stream-word gap on its line, so the line needs a
second stream word, and (d) one span style owns every letter: exactly one
span that contains the letter, no superscript flag, one font, and sizes and
baselines within tolerance.  Every fixture is synthetic.
"""
from pathlib import Path

import pytest

import rag
from test_letter_spaced_text_layer import (
    _FONT, _off_spaced, _pdf, _put, _run, _write_line)

pymupdf = pytest.importorskip("pymupdf")

_BLUE = (0.0, 0.0, 0.4)


def _stream_words(path: Path):
    with pymupdf.open(path) as pdf:
        page = pdf[0]
        words = page.get_text("words", sort=True)
        spans = [
            span for block in page.get_text("dict", sort=True)["blocks"]
            for line in block.get("lines", []) for span in line["spans"]
        ]
        return rag._content_stream_letter_words(page, words, spans)


def _positioning_only(page):
    # Without space glyphs the content stream proves no word boundary.
    x = 20.0
    for word in ("The", "ferry", "crew", "sorts"):
        x = _put(page, x, 100, word, 12.0) + 3.5
    for word in ("off", "its"):
        for character in word:
            x = _put(page, x, 100, character, 12.0) + 2.4
        x += 1.1
    _put(page, x, 100, "cargo", 12.0)


def _glued(inner: float, *, justified: float = 0.0):
    """``offits`` is one stream word whose inner gap is ``inner`` wide.

    ``justified`` widens only the line's first word gap, so ``inner`` can lie
    between its narrowest and its widest stream-word gap.
    """
    def write(page):
        x = _put(page, 20.0, 100, "The ", 12.0) + justified
        x = _put(page, x, 100, "ferry crew sorts ", 12.0)
        for index, character in enumerate("offits"):
            x = _put(page, x, 100, character, 12.0)
            x += inner if index == 2 else 2.4
        _put(page, x - 2.4, 100, " cargo net.", 12.0)
    return write


def _spaced(run: str):
    """Place ``run`` glyph by glyph inside an ordinary line."""
    return lambda page: _write_line(page, 100, (
        ("The ferry crew sorts ", False), (run, True),
        (" its cargo net.", False)))


def _longer_stream_word(page):
    # ``o`` ``f`` are spaced, but the content-stream word is ``offs``.
    x = _put(page, 20.0, 100, "The ferry crew sorts ", 12.0)
    for character in "of":
        x = _put(page, x, 100, character, 12.0) + 2.4
    _put(page, x, 100, "fs its cargo net.", 12.0)


def _odd_middle(*, size: float = 12.0, rise: float = 0.0,
                font: str = _FONT, **style):
    """Space ``o f f`` by glyph; only its middle letter takes the style."""
    def write(page):
        x = _put(page, 20.0, 100, "The ferry crew sorts ", 12.0)
        for index, character in enumerate("off"):
            odd = index == 1
            page.insert_text(
                (x, 100 - (rise if odd else 0.0)), character,
                fontname=font if odd else _FONT,
                fontsize=size if odd else 12.0, **(style if odd else {}))
            x += pymupdf.get_text_length(
                character, fontname=font if odd else _FONT,
                fontsize=size if odd else 12.0) + 2.4
        _put(page, x - 2.4, 100, " its cargo net.", 12.0)
    return write


def _over_larger_type(x: float, text: str):
    """``_off_spaced`` over 24pt type whose box reaches the run's centers."""
    def write(page):
        _off_spaced(page)
        _put(page, x, 118, text, 24.0)
    return write


@pytest.mark.parametrize("write", [
    _run, _off_spaced, _odd_middle(color=_BLUE),
    _odd_middle(size=11.5, rise=0.8),
    _over_larger_type(60.0, "ab" + " " * 10 + "cd"),
], ids=["two-runs", "one-run", "other-color", "unflagged-small-rise",
        "tall-span-without-the-letters"])
def test_proven_letter_runs_merge_into_content_stream_words(tmp_path, write):
    # Positive controls: color is not a style proof, MuPDF flags neither a
    # slightly smaller nor a slightly raised letter within tolerance, and a
    # tall span below that lacks the letters does not own them.
    words = _stream_words(_pdf(tmp_path, write))

    assert [str(word[4]) for word in words][:8] == [
        "The", "ferry", "crew", "sorts", "off", "its", "cargo", "net."]


def test_a_merged_word_spans_its_letters(tmp_path):
    # The merged word takes the union of its letters' boxes and the first
    # letter's block, line and word numbers.
    path = _pdf(tmp_path, _off_spaced)
    with pymupdf.open(path) as pdf:
        letters = pdf[0].get_text("words", sort=True)[4:7]
    merged = _stream_words(path)[4]

    assert [str(word[4]) for word in letters] == ["o", "f", "f"]
    assert tuple(merged) == (
        letters[0][0], min(word[1] for word in letters), letters[-1][2],
        max(word[3] for word in letters), "off", *letters[0][5:])


@pytest.mark.parametrize("write", [
    _positioning_only, _glued(6.0), _glued(4.5, justified=11.0),
    _longer_stream_word,
    lambda page: _write_line(page, 100, (("off", True),)),
    _over_larger_type(121.0, "XYZ"),
    _over_larger_type(60.0, "of" + " " * 10 + "fs"),
    _odd_middle(size=8.0), _odd_middle(size=16.0),
    _odd_middle(font="helv"), _odd_middle(size=11.5, rise=1.2),
    _odd_middle(rise=1.9), _odd_middle(rise=-2.0, color=_BLUE),
    _spaced("o\u00dff"), _spaced("1995"), _spaced("off,"),
], ids=["positioning-only", "wide-gap", "uneven-word-gaps",
        "longer-stream-word", "single-word-line", "second-stream-word-owner",
        "second-span-owner", "smaller-letter", "larger-letter", "other-font",
        "superscript-flag", "raised-letter", "lowered-own-span",
        "non-ascii-letter", "digits", "trailing-comma"])
def test_unproven_letter_runs_are_not_merged(tmp_path, write):
    # (a) a second stream word covers the first letter; (b) the stream word
    # spells other letters; (c) an inner gap is at least as wide as some word
    # gap on the line, even one narrower than its widest, or the line has no
    # word gap; (d) a letter has a second owning span, another size, font
    # or baseline, or a superscript flag; and only ASCII letters form runs,
    # not digits or punctuation.
    assert _stream_words(_pdf(tmp_path, write)) is None
