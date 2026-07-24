from pathlib import Path

import pytest

import rag


@pytest.mark.parametrize("argv", [
    ["chunk", "--structure-profile", "unknown-layout"],
    ["full", "--pdf", "Book.pdf", "--structure-profile", "unknown-layout"],
    ["batch", "Book.pdf", "--structure-profile", "unknown-layout"],
])
def test_ingestion_cli_rejects_unknown_profile_during_parsing(argv):
    with pytest.raises(SystemExit) as error:
        rag.main(argv)
    assert error.value.code == 2


@pytest.mark.parametrize("command", ["chunk", "full", "batch"])
def test_ingestion_cli_forwards_registered_profile(
        monkeypatch, tmp_path, command):
    observed = []
    profile = "roman-parts-book-v1"
    pdf = tmp_path / "Book.pdf"
    pdf.write_bytes(b"fixture")
    paths = rag._output_paths_for_name("Book")

    monkeypatch.setattr(rag, "_validate_api_key", lambda _model: None)
    monkeypatch.setattr(
        rag, "chunk_document",
        lambda *_args, **kwargs: observed.append(kwargs["structure_profile"]))

    def fake_pipeline(_pdf, args, **_kwargs):
        observed.append(args.structure_profile)
        return paths, {"collection": "book", "db_dir": paths["chroma"]}

    monkeypatch.setattr(rag, "_run_pipeline_job", fake_pipeline)
    argv = {
        "chunk": ["chunk", "--structure-profile", profile],
        "full": ["full", "--pdf", str(pdf),
                 "--structure-profile", profile],
        "batch": ["batch", str(pdf), "--structure-profile", profile],
    }[command]

    rag.main(argv)

    assert observed == [profile]


def test_interactive_chunk_menu_serializes_profile(monkeypatch):
    observed = {}

    def choose(prompt, _options, **_kwargs):
        if prompt.startswith("What would"):
            return "chunk"
        if prompt.startswith("Chunk size"):
            return str(rag.DEFAULT_MAX_TOKENS)
        if prompt.startswith("Document structure"):
            return "roman-parts-book-v1"
        raise AssertionError(f"unexpected menu prompt: {prompt}")

    monkeypatch.setattr(rag, "_menu_choose", choose)
    monkeypatch.setattr(rag, "_menu_file", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rag, "_menu_yesno", lambda *_args, **_kwargs: False)
    def capture(args, **_kwargs):
        observed["args"] = list(args)
        return 0

    monkeypatch.setattr(rag, "_run_rag_entrypoint", capture)

    rag.interactive_menu()

    args = observed["args"]
    index = args.index("--structure-profile")
    assert args[index + 1] == "roman-parts-book-v1"
    assert Path(args[0]).name == "chunk"
