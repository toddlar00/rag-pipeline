"""A body-text item that is only a number must keep its line in its chunk.

The generic cleaner deletes every standalone 1-4 digit line as a page number.
Fresh OCR can label an opinion's numbered part marker ("1", "2") as ordinary
body text; HybridChunker merges it with the paragraphs around it, so the
cleaner erased only its line while lineage still bound the item, and the
corpus gate failed ``source_token_fidelity``. (The existing numeric rescue
retries only when the cleaner erased the whole source-bound fragment.)

A standalone number line is now kept as many times as distinct ordinary
body-text items of the fragment consist of exactly that number. Running page
furniture, non-text labels and furniture layers never attest a line, and a
fragment without such an item normalizes exactly as before. All text is
synthetic.
"""

from types import SimpleNamespace

import rag


def _item(ref, text, *, label="text", layer="body"):
    return SimpleNamespace(self_ref=ref, label=SimpleNamespace(value=label),
                           text=text, orig=text,
                           content_layer=SimpleNamespace(value=layer))


FIRST = "The agency adopted the rule after notice and comment."
SECOND = "The court then turned to the statute itself."
TEXT = f"{FIRST}\n\n1\n\n{SECOND}"
ITEMS = [_item("#/texts/1", FIRST), _item("#/texts/2", "1"),
         _item("#/texts/3", SECOND)]


def _normalize(text, items, numbers):
    return rag._normalize_source_chunk_text(
        text, "body", preserve_source_identity=True, source_items=items,
        source_attested_numbers=numbers)


def test_an_attested_number_line_is_kept_in_a_merged_fragment():
    numbers = rag._source_attested_number_lines(ITEMS, excluded_refs=())

    assert numbers == {"1": 1}
    assert _normalize(TEXT, ITEMS, numbers) == TEXT


def test_without_an_attested_item_the_number_line_is_still_removed():
    assert _normalize(TEXT, ITEMS, None) == f"{FIRST}\n\n{SECOND}"
    assert rag._normalize_source_chunk_text(
        TEXT, "body", preserve_source_identity=True,
        source_items=ITEMS) == f"{FIRST}\n\n{SECOND}"


def test_only_as_many_lines_as_attesting_items_are_kept():
    text = f"1\n\n{FIRST}\n\n1\n\n{SECOND}"

    assert _normalize(text, ITEMS, {"1": 1}) == f"1\n\n{FIRST}\n\n{SECOND}"


def test_other_number_lines_are_still_removed():
    text = f"{FIRST}\n\n1\n\n{SECOND}\n\n38"

    assert _normalize(text, ITEMS, {"1": 1}) == TEXT


def test_running_furniture_labels_and_layers_never_attest():
    items = [
        _item("#/texts/1", "4"),                        # running page number
        _item("#/texts/2", "5", label="page_footer"),
        _item("#/texts/3", "6", layer="furniture"),
        _item("#/texts/4", "1.2"),                      # not a bare number
        _item("#/texts/5", "12345"),                    # longer than a folio
        _item("#/texts/6", " 7 "),
        _item("#/texts/6", " 7 "),                      # one ref counts once
    ]

    numbers = rag._source_attested_number_lines(
        items, excluded_refs={"#/texts/1"})

    assert numbers == {"7": 1}


def test_the_allowance_does_not_outlive_its_call():
    _normalize(TEXT, ITEMS, {"1": 1})

    assert rag._strip_headers_footers(TEXT) == f"{FIRST}\n\n{SECOND}"
