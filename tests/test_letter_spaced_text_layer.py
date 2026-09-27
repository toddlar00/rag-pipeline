"""Letter-spaced scanner text layers rejoin only content-stream words.

A scanner layer can place each glyph separately; PyMuPDF then splits Docling's
text into isolated letters that quality rejects as ``ocr_gibberish``.  Only an
item that fails that detector today, and that no source relation places in an
exempt record, is repaired again with content-stream words.  The result is
committed after the native group stages, and only for a ref no recovered or
deferred group names.  ``test_letter_spaced_merge_proofs.py`` pins the proofs
that merge letters into content-stream words.  Every fixture is synthetic.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import rag

pymupdf = pytest.importorskip("pymupdf")

_FONT = "tiro"
_REF = "#/texts/0"
_GROUP_STAGES = (
    "_recover_overlapping_native_text_groups",
    "_recover_adjacent_native_split_word_groups",
    "_recover_cross_page_native_word_groups",
    "_recover_disjoint_section_flow_groups")


def _put(page, x: float, y: float, text: str, size: float) -> float:
    page.insert_text((x, y), text, fontname=_FONT, fontsize=size)
    return x + pymupdf.get_text_length(text, fontname=_FONT, fontsize=size)


def _write_line(page, y: float, pieces, *, size: float = 12.0,
                letter_gap: float = 2.4) -> float:
    """Write (text, letter_spaced) pieces; spaced text is placed by glyph."""
    x = 20.0
    for text, letter_spaced in pieces:
        if not letter_spaced:
            x = _put(page, x, y, text, size)
            continue
        for index, character in enumerate(text):
            x = _put(page, x, y, character, size)
            if index < len(text) - 1:
                x += letter_gap
    return x


def _box(bbox=(15.0, 84.0, 400.0, 106.0), page: int = 1):
    left, top, right, bottom = bbox
    return SimpleNamespace(page_no=page, bbox=SimpleNamespace(
        l=left, t=top, r=right, b=bottom, coord_origin="TOPLEFT"))


def _item(text: str, bbox=(15.0, 84.0, 400.0, 106.0), *,
          parent: str = "#/body", label: str = "text",
          layer: str = "body", extra_prov=()):
    return SimpleNamespace(
        self_ref=_REF, label=label, content_layer=layer, text=text,
        orig=text, parent=SimpleNamespace(cref=parent),
        prov=[_box(bbox), *extra_prov])


def _pdf(tmp_path: Path, write) -> Path:
    path = tmp_path / "layer.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=420, height=200)
        write(page)
        pdf.save(path)
    return path


def _document(item, **collections):
    return SimpleNamespace(texts=[item], **{
        name: list(collections.get(name, ()))
        for name in ("groups", "pictures", "tables", "key_value_items",
                     "form_items")})


def _today(tmp_path: Path, write, item):
    edits, rebuild = {}, set()
    repairs = rag._recover_native_text_repairs(
        _document(item), _pdf(tmp_path, write), repair_edits=edits,
        rebuild_refs=rebuild)
    return repairs, edits, rebuild


def _repair(tmp_path: Path, write, item, *, claimed=frozenset(),
            structural_ranges=None, document=None):
    edits, rebuild, retries = {}, set(), {}
    repairs = rag._recover_native_text_repairs(
        document or _document(item), _pdf(tmp_path, write),
        repair_edits=edits, rebuild_refs=rebuild,
        structural_ranges=structural_ranges, letter_spaced_retries=retries)
    rag._apply_letter_spaced_layer_retries(
        retries, text_overrides=repairs, repair_edits=edits,
        rebuild_refs=rebuild, claimed_refs=set(claimed))
    return repairs, edits, rebuild, retries


_RUN_SOURCE = "The ferry crew sorts  of f  i t s cargo net."
_TODAY = "The ferry crew sorts o f f i t s cargo net."
_OFF_ONLY = "The ferry crew sorts o f f its cargo net."
_REJOINED = "The ferry crew sorts off its cargo net."
_REJOINED_EDITS = (("of f", "off"), ("i t s", "its"))


def _run(page):
    _write_line(page, 100, (
        ("The ferry crew sorts ", False), ("off", True), (" ", False),
        ("its", True), (" cargo net.", False)))


def _off_spaced(page):
    _write_line(page, 100, (
        ("The ferry crew sorts ", False), ("off", True),
        (" its cargo net.", False)))


def _flat(value: str) -> str:
    return " ".join(value.split())


def _plain(page):
    _write_line(page, 100, ((_REJOINED, False),))


@pytest.mark.parametrize(("write", "source", "expected"), [
    (_run, _RUN_SOURCE, (_TODAY, (("of", "o f"),), set())),
    (_plain, "The ferry crew sorts  off  its cargo net.", (
        _REJOINED, (("sorts  off", "sorts off"), ("off  its", "off its")),
        {_REF})),
], ids=["letter-spaced-layer", "plain-layer"])
def test_default_native_repair_output_is_unchanged(
        tmp_path, write, source, expected):
    # Characterization: callers that do not request retries keep today's
    # output: the re-split that quality rejects, and the OCR fallback's
    # localized repair of an extraction-spaced item with its rebuild.
    repairs, edits, rebuild = _today(tmp_path, write, _item(source))

    assert (_flat(repairs[_REF]), edits[_REF], rebuild) == expected


@pytest.mark.parametrize("label", ["text", "list_item"])
def test_letter_spaced_layer_run_rejoins_content_stream_words(
        tmp_path, label):
    repairs, edits, _, retries = _repair(
        tmp_path, _run, _item(_RUN_SOURCE, label=label))

    assert list(retries) == [_REF]
    assert _flat(repairs[_REF]) == _REJOINED
    assert edits[_REF] == _REJOINED_EDITS


def test_tall_boxes_of_an_adjacent_line_do_not_block_the_join(tmp_path):
    # Scanner layers report tall word boxes that reach into the previous
    # line, whose larger type fails the generic bbox-overlap style check.
    def write(page):
        _write_line(page, 90, (("Above the run sits larger type", False),),
                    size=14.0)
        _run(page)

    repairs, _, _, _ = _repair(
        tmp_path, write, _item(_RUN_SOURCE, (15.0, 93.0, 400.0, 104.0)))

    assert "sorts off its cargo" in _flat(repairs[_REF])


def test_passing_two_letter_split_keeps_todays_output(tmp_path):
    # Failing-only: a two-letter native split passes quality today.
    def write(page):
        _write_line(page, 100, (
            ("Once ", False), ("it", True),
            (" rains, drains overflow.", False)))

    item = _item("Once it rains, drains overflow.")
    today = _today(tmp_path, write, item)
    repairs, edits, rebuild, retries = _repair(tmp_path, write, item)

    assert today[0][_REF] == "Once i t rains, drains overflow."
    assert retries == {}
    assert (repairs, edits, rebuild) == today


@pytest.mark.parametrize(("write", "source", "today_text"), [
    (_off_spaced, _TODAY, _OFF_ONLY), (_run, _OFF_ONLY, _TODAY),
    (_run, _TODAY, None),
], ids=["only-the-source-fails", "only-today-fails", "no-override-today"])
def test_the_gate_reads_todays_final_text(
        tmp_path, write, source, today_text):
    # Failing-only is decided on today's repaired text, not on the source,
    # and a retry can add the override today's repair does not make.
    item = _item(source)
    today = _today(tmp_path, write, item)
    repairs, edits, rebuild, retries = _repair(tmp_path, write, item)

    assert _flat(today[0].get(_REF, "")) == (today_text or "")
    if today_text == _OFF_ONLY:
        assert retries == {}
        assert (repairs, edits, rebuild) == today
    else:
        assert list(retries) == [_REF]
        assert _flat(repairs[_REF]) == _REJOINED


@pytest.mark.parametrize(("label", "layer", "parent"), [
    ("text", "body", "#/pictures/0"),
    ("text", "body", "#/tables/0"),
    ("section_header", "body", "#/body"),
    ("page_header", "furniture", "#/body"),
    ("text", "furniture", "#/body"),
    ("footnote", "body", "#/body"),
    ("caption", "body", "#/body"), ("code", "body", "#/body"),
])
def test_text_outside_gated_records_keeps_todays_output(
        tmp_path, label, layer, parent):
    # Quality exempts figure/table records from ocr_gibberish, headings or
    # furniture never reach record text, and other labels are out of scope.
    item = _item(_RUN_SOURCE, parent=parent, label=label, layer=layer)
    repairs, _, _, retries = _repair(tmp_path, _run, item)

    assert retries == {}
    assert _flat(repairs[_REF]) == _TODAY


def _node(ref: str, parent: str, **relations):
    return SimpleNamespace(
        self_ref=ref, parent=SimpleNamespace(cref=parent), **{
            name: [SimpleNamespace(cref=child)
                   for child in relations.get(name, ())]
            for name in ("children", "captions", "footnotes")})


@pytest.mark.parametrize(("owner", "relation"), [
    ("#/pictures/0", "nested_group"), ("#/tables/0", "nested_group"),
    ("#/pictures/0", "form_item"), ("#/tables/0", "key_value_item"),
    ("#/pictures/0", "captions"), ("#/tables/0", "captions"),
    ("#/pictures/0", "footnotes"), ("#/tables/0", "footnotes"),
    ("#/pictures/0", "children"), ("#/body", "nested_group"),
])
def test_the_whole_ancestry_decides_the_exemption(tmp_path, owner, relation):
    # A picture or table reaching the item through enclosing groups, a form
    # or key-value item, or a caption, footnote or child relation can put it
    # in an exempt record.  Each link is given in one direction only (the
    # item's parent, the owner's relation); body groups do not exempt.
    nested = {"nested_group": "#/groups/1", "form_item": "#/form_items/0",
              "key_value_item": "#/key_value_items/0"}
    item = _item(_RUN_SOURCE, parent=nested.get(relation, "#/body"))
    collections = {
        "groups": [_node("#/groups/0", owner, children=["#/groups/1"]),
                   _node("#/groups/1", "#/groups/0", children=[_REF])]
        if relation == "nested_group" else []}
    if relation.endswith("_item"):
        collections[f"{relation}s"] = [_node(nested[relation], owner)]
    if owner != "#/body":
        collections["tables" if "tables" in owner else "pictures"] = [
            _node(owner, "#/body", **(
                {} if relation.endswith("_item")
                else {"children": ["#/groups/0"]}
                if relation == "nested_group" else {relation: [_REF]}))]
    repairs, _, _, retries = _repair(
        tmp_path, _run, item, document=_document(item, **collections))

    exempt = owner != "#/body"
    assert list(retries) == ([] if exempt else [_REF])
    assert _flat(repairs[_REF]) == (_TODAY if exempt else _REJOINED)


@pytest.mark.parametrize("detector", [
    "_detect_aligned_list_tables", "_running_section_heading_refs"])
def test_aligned_list_members_and_running_banners_keep_todays_output(
        tmp_path, monkeypatch, detector):
    # An aligned-list table publishes as a pipe table, which quality exempts,
    # and a running banner never publishes; both can pass today.
    item = _item(_RUN_SOURCE)
    calls: list[object] = []

    def detect(dl_doc, **_kwargs):
        calls.append(dl_doc)
        if detector == "_detect_aligned_list_tables":
            return [rag.SyntheticLayoutTable(markdown="| a |", items=(item,))]
        return {_REF}

    monkeypatch.setattr(rag, detector, detect)
    repairs, _, _, retries = _repair(tmp_path, _run, item)

    assert len(calls) == 1
    assert retries == {}
    assert _flat(repairs[_REF]) == _TODAY


@pytest.mark.parametrize("scope", ["multi_provenance", "structural_range"])
def test_out_of_scope_items_keep_todays_output(tmp_path, scope):
    # Page fragments of a multi-provenance item can split its letter run,
    # and structural ranges are not quality-eligible.
    item = _item(_RUN_SOURCE, extra_prov=(
        [_box((15.0, 150.0, 400.0, 170.0))]
        if scope == "multi_provenance" else []))
    today = _today(tmp_path, _run, item)
    repairs, edits, rebuild, retries = _repair(
        tmp_path, _run, item, structural_ranges=(
            {(1, 1)} if scope == "structural_range" else None))

    assert _flat(today[0][_REF]) == _TODAY
    assert retries == {}
    assert (repairs, edits, rebuild) == today


def test_a_retry_equal_to_the_source_drops_todays_override():
    # A retry that asks for no rebuild keeps today's rebuild flag.
    overrides = {_REF: _TODAY, "#/texts/1": "kept", "#/texts/2": "x"}
    edits = {_REF: (("of", "o f"),)}
    rebuild = {"#/texts/2"}
    retry = rag._LetterSpacedLayerRetry(
        text="a b", edits=(), rebuild=False, source_text="a b")
    rag._apply_letter_spaced_layer_retries(
        {_REF: retry, "#/texts/2": retry}, text_overrides=overrides,
        repair_edits=edits, rebuild_refs=rebuild, claimed_refs=set())

    assert (overrides, edits, rebuild) == (
        {"#/texts/1": "kept"}, {}, {"#/texts/2"})


_PLURAL = (_TODAY.replace("net", "nets"),
           _REJOINED.replace("net", "nets"))


@pytest.mark.parametrize(
    ("today", "retry", "extra_edits", "fallback", "rebuild"), [
        (None, _REJOINED.replace("off", "oaf"), (), False, None),
        (None, _TODAY.replace("o f", "of"), (), False, None),
        (None, _REJOINED, (), False, set()),
        (None, _REJOINED, (), True, {_REF}),
        (None, _REJOINED, (("  ", " "),), False, {_REF}),
        (None, _REJOINED, _REJOINED_EDITS[:1], False, {_REF}),
        (*_PLURAL, (), False, set()),
    ], ids=["other-lexemes", "still-failing", "local-edits", "fallback",
            "repeated-edit", "duplicate-edit", "todays-lexemes"])
def test_a_retry_commits_only_under_the_commit_rules(
        tmp_path, monkeypatch, today, retry, extra_edits, fallback, rebuild):
    # The retry must clear the run and keep today's lexemes, which can differ
    # from the source's; its own OCR fallback, an edit that recurs in the
    # source or a duplicate edit asks for a rebuild, as today's repairs do.
    original = rag._repair_native_text_item
    calls: list[list[tuple]] = []

    def scripted(item, source_text, *, native_words, **context):
        calls.append(native_words)
        if len(calls) == 2:
            return retry, [*_REJOINED_EDITS, *extra_edits], fallback
        if today is None:
            return original(item, source_text, native_words=native_words,
                            **context)
        return today, [], False

    monkeypatch.setattr(rag, "_repair_native_text_item", scripted)
    repairs, edits, rebuilt, retries = _repair(
        tmp_path, _run, _item(_RUN_SOURCE))

    assert len(calls) == 2
    if rebuild is None:
        assert retries == {}
        assert _flat(repairs[_REF]) == _TODAY
    else:
        assert (repairs[_REF], edits[_REF], rebuilt) == (
            retry, _REJOINED_EDITS, rebuild)


# -- Production wiring ------------------------------------------------------


def _group_stages(monkeypatch, path: Path, *, claim_stage=None, seen=None):
    """Bind the snapshot to ``path`` and replace the four group stages."""
    @contextmanager
    def snapshot(*_args, **_kwargs):
        yield SimpleNamespace(
            pdf=SimpleNamespace(path=path), source_path=path,
            manifest_input=lambda: None)

    group = rag.SourceTextGroupRecovery(
        text=_REJOINED, refs=(_REF,), page=1)

    def stage(name):
        def recover(_dl_doc, _path, **kwargs):
            if seen is not None and "text_overrides" in kwargs:
                seen[name] = dict(kwargs["text_overrides"])
            return (group,) if name == claim_stage else ()
        return recover

    monkeypatch.setattr(rag, "_open_docling_source_pdf_snapshot", snapshot)
    for name in _GROUP_STAGES:
        monkeypatch.setattr(rag, name, stage(name))
    return group


def _enrich(tmp_path, document, **kwargs):
    return rag._recover_bound_source_enrichments(
        document, tmp_path / "doc.json", None, **kwargs)


def _committed(enrichments):
    return (enrichments.text_overrides, enrichments.text_repair_edits,
            enrichments.text_rebuild_refs)


def test_bound_enrichments_commit_the_retry_after_the_group_stages(
        tmp_path, monkeypatch):
    item = _item(_RUN_SOURCE)
    repairs, edits, rebuild = _today(tmp_path, _run, item)
    seen: dict[str, dict[str, str]] = {}
    _group_stages(monkeypatch, _pdf(tmp_path, _run), seen=seen)

    enrichments = _enrich(tmp_path, _document(item))
    inside = _enrich(tmp_path, _document(item), structural_ranges={(1, 1)})

    assert _flat(enrichments.text_overrides[_REF]) == _REJOINED
    assert enrichments.text_repair_edits[_REF] == _REJOINED_EDITS
    # Today's repair needs no rebuild.  The retry's OCR fallback asks for
    # one, and two edits of the retry do not occur in the source.
    assert (edits[_REF], rebuild) == ((("of", "o f"),), set())
    assert enrichments.text_rebuild_refs == frozenset({_REF})
    # The group stages decide on today's overrides.
    assert seen == {
        "_recover_adjacent_native_split_word_groups": repairs,
        "_recover_cross_page_native_word_groups": repairs,
    }
    # The stage receives the structural ranges.
    assert _committed(inside) == (repairs, edits, frozenset(rebuild))


@pytest.mark.parametrize("stage", _GROUP_STAGES)
def test_bound_enrichments_skip_a_ref_any_group_stage_claims(
        tmp_path, monkeypatch, stage):
    item = _item(_RUN_SOURCE)
    today = _today(tmp_path, _run, item)
    group = _group_stages(monkeypatch, _pdf(tmp_path, _run), claim_stage=stage)

    enrichments = _enrich(tmp_path, _document(item))

    assert enrichments.text_group_recoveries == (group,)
    assert _committed(enrichments) == (
        today[0], today[1], frozenset(today[2]))


@pytest.mark.parametrize("member", [True, False])
def test_bound_enrichments_skip_deferred_multi_block_members(
        tmp_path, monkeypatch, member):
    # A deferred multi-block group is admitted only by the order replay.  Its
    # members keep today's text in both passes, so the retry cannot change
    # whether that replay runs; other refs are still retried.
    item = _item(_RUN_SOURCE)
    today = _today(tmp_path, _run, item)
    _group_stages(monkeypatch, _pdf(tmp_path, _run))
    refs = (_REF, "#/texts/9") if member else ("#/texts/6", "#/texts/9")
    group = rag.SourceTextGroupRecovery(text="x y", refs=refs, page=1)

    def overlapping(_dl_doc, _path, *, reading_order_violations=frozenset(),
                    deferred_groups=None, **_kwargs):
        if reading_order_violations:
            return (group,)
        deferred_groups.append(group)
        return ()

    monkeypatch.setattr(
        rag, "_recover_overlapping_native_text_groups", overlapping)
    first = _enrich(tmp_path, _document(item))
    replay = _enrich(
        tmp_path, _document(item),
        reading_order_violations=frozenset({(refs[0], 1)}))

    assert first.deferred_text_groups == (group,)
    assert replay.text_group_recoveries == (group,)
    for enrichments in (first, replay):
        if member:
            assert _committed(enrichments) == (
                today[0], today[1], frozenset(today[2]))
        else:
            assert _flat(enrichments.text_overrides[_REF]) == _REJOINED


def test_bound_enrichments_drop_retries_when_the_native_stage_fails(
        tmp_path, monkeypatch):
    item = _item(_RUN_SOURCE)
    _group_stages(monkeypatch, _pdf(tmp_path, _run))
    calls: list[dict] = []

    def failing(_dl_doc, _path, **kwargs):
        calls.append(kwargs)
        kwargs["letter_spaced_retries"][_REF] = rag._LetterSpacedLayerRetry(
            text=_REJOINED, edits=_REJOINED_EDITS, rebuild=True,
            source_text=_RUN_SOURCE)
        raise RuntimeError("native stage failed")

    monkeypatch.setattr(rag, "_recover_native_text_repairs", failing)
    enrichments = _enrich(tmp_path, _document(item))

    assert len(calls) == 1
    assert _committed(enrichments) == ({}, {}, frozenset())
