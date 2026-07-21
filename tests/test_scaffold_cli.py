from pathlib import Path

import pytest

import scaffold_to_markdown as scaffold_md


@pytest.mark.parametrize('flag', ['-o', '--out'])
def test_parser_accepts_output_flags(flag):
    args = scaffold_md.parse_args([
        'book_scaffold.json',
        'book.pdf',
        flag,
        'book.md',
    ])

    assert args.scaffold == Path('book_scaffold.json')
    assert args.pdf == Path('book.pdf')
    assert args.out == Path('book.md')


def test_main_forwards_explicit_output_without_opening_pdf(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scaffold_md,
        'process_book',
        lambda scaffold, pdf, output=None: calls.append((scaffold, pdf, output)),
    )

    result = scaffold_md.main([
        'book_scaffold.json',
        'book.pdf',
        '--out',
        'rendered.md',
    ])

    assert result == 0
    assert calls == [('book_scaffold.json', 'book.pdf', 'rendered.md')]


def test_discovery_prefers_exact_pdf_stem(tmp_path):
    scaffold_dir = tmp_path / 'generated'
    pdf_dir = tmp_path / 'sources'
    scaffold_dir.mkdir()
    pdf_dir.mkdir()
    scaffold = scaffold_dir / 'Civil Procedure_scaffold.json'
    exact_pdf = pdf_dir / 'Civil Procedure.pdf'
    scaffold.write_text('{}', encoding='utf-8')
    exact_pdf.touch()
    (pdf_dir / 'Civil Procedure_OCR.pdf').touch()

    assert scaffold_md.discover_book_pairs(tmp_path) == [
        (scaffold, exact_pdf),
    ]


def test_all_mode_discovers_workspace_and_uses_output_directory(
        monkeypatch, tmp_path):
    scaffold = tmp_path / 'generated' / 'Property_scaffold.json'
    pdf = tmp_path / 'uploads' / 'Property_OCR.pdf'
    scaffold.parent.mkdir()
    pdf.parent.mkdir()
    scaffold.write_text('{}', encoding='utf-8')
    pdf.touch()
    calls = []
    monkeypatch.setattr(
        scaffold_md,
        'process_book',
        lambda scaffold, pdf, output=None: calls.append((scaffold, pdf, output)),
    )
    output_dir = tmp_path / 'markdown'

    result = scaffold_md.main([
        '--all',
        '--root',
        str(tmp_path),
        '--out',
        str(output_dir),
    ])

    assert result == 0
    assert calls == [(str(scaffold), str(pdf), str(output_dir / 'Property.md'))]
    assert output_dir.is_dir()


def test_default_output_is_scaffold_relative(tmp_path):
    scaffold = tmp_path / 'generated' / 'Evidence_scaffold.json'

    output = scaffold_md._default_output_path(
        scaffold,
        {'filename': 'Evidence.pdf'},
    )

    assert output == scaffold.parent / 'Evidence.md'


def test_final_toc_entry_reaches_end_of_document():
    entry = scaffold_md.FlatEntry(
        level=0,
        entry_type='section',
        title='Appendix',
        page_pdf=20,
        page_book=20,
    )

    scaffold_md.compute_page_ranges([entry], total_pages=125)

    assert entry.page_pdf_start == 20
    assert entry.page_pdf_end == 125
