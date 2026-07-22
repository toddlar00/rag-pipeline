#!/usr/bin/env python3
"""
ui.py -- Gradio web UI for the RAG pipeline.

Usage:
    python ui.py --chunks output/Book/Book_chunks.jsonl \
        --db output/Book/Book_chroma --collection book
    python ui.py --db-backend qdrant --db output/Book/Book_qdrant \
        --chunks output/Book/Book_chunks.jsonl --collection book
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

import gradio as gr

# Import pipeline functions
sys.path.insert(0, str(Path(__file__).parent))
import rag


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_config = {
    # Required CLI values populate these before the app is built. Keeping the
    # unconfigured state explicit avoids silently targeting obsolete flat
    # output paths when this module is embedded programmatically.
    "chunks_path": None,
    "db_path": None,
    "db_backend": "chroma",
    "collection": None,
    "embedding_model": "nomic-ai/nomic-embed-text-v2-moe",
}


def _load_chunk_metadata() -> dict:
    """Load chunk stats for filter dropdowns."""
    chunks_path = _config["chunks_path"]
    if not isinstance(chunks_path, Path) or not chunks_path.exists():
        return {"chapters": [], "types": [], "total": 0}

    chapters = set()
    types = set()
    total = 0
    for enc in ("utf-8", "latin-1"):
        try:
            with open(chunks_path, encoding=enc) as f:
                for line in f:
                    if line.strip():
                        rec = json.loads(line)
                        total += 1
                        ch = rec["metadata"].get("chapter_num")
                        if ch is not None:
                            chapters.add(ch)
                        types.add(rec["metadata"]["content_type"])
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

    return {
        "chapters": sorted(chapters),
        "types": sorted(types),
        "total": total,
    }


# ---------------------------------------------------------------------------
# Search tab
# ---------------------------------------------------------------------------

def do_search(query, content_type, chapter, n_results, hybrid, use_reranker,
              db_backend=None):
    """Run search and return formatted results."""
    if not query.strip():
        return "Enter a search query."

    # Read from config without mutating (thread safety for concurrent requests)
    db_path = _config["db_path"]
    chunks_path = _config["chunks_path"]
    if (not isinstance(db_path, Path)
            or not isinstance(chunks_path, Path)
            or not _config["collection"]):
        return "UI is not configured with chunks, database, and collection paths."

    ct = content_type if content_type != "All" else None
    ch = int(chapter) if chapter and chapter != "All" else None
    if isinstance(hybrid, str):
        hybrid = {
            "Auto (recommended)": None,
            "Hybrid": True,
            "Vector only": False,
        }.get(hybrid, None)
    if isinstance(use_reranker, str):
        use_reranker = {
            "Auto (recommended)": None,
            "Always": True,
            "Never": False,
        }.get(use_reranker, None)

    t0 = time.time()

    try:
        # One configured database path has one backend. Keep the optional
        # argument for callers of the old function signature, but never let a
        # UI selection reinterpret the configured path as another backend.
        configured_backend = _config["db_backend"]
        response = rag.search_index(
            query,
            db_path,
            db_backend=configured_backend,
            n_results=int(n_results),
            content_type=ct,
            chapter_num=ch,
            collection_name=_config["collection"],
            embedding_model=_config["embedding_model"],
            use_reranker=use_reranker,
            hybrid=hybrid,
            chunks_path=chunks_path,
        )

        elapsed = time.time() - t0
        mode = response.effective_mode
        if response.reranker_applied:
            mode += " + reranked"

        # Format output
        lines = [
            f"**{len(response.hits)} results** ({mode}, {elapsed:.1f}s)\n"
        ]
        for warning in response.warnings:
            lines.append(f"> Warning: {warning}\n")

        for i, hit in enumerate(response.hits):
            doc = hit.text
            meta = hit.metadata
            ct_val = meta.get("content_type", "?")
            section = meta.get("section_path", "")
            case = meta.get("primary_case", "")
            pages = meta.get("page_range", "")
            ctx = meta.get("context", "")

            lines.append(f"### Result {i+1} (score: {hit.score:.3f})")
            lines.append(f"**Type:** {ct_val} | **Pages:** {pages}")
            if section:
                lines.append(f"**Section:** {section}")
            if case:
                lines.append(f"**Case:** {case}")
            if ctx:
                lines.append(f"*Context: {ctx}*")
            lines.append(f"\n{doc[:500]}{'...' if len(doc) > 500 else ''}\n")
            lines.append("---")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Export tab
# ---------------------------------------------------------------------------

def do_export(include_types, exclude_structural, chapters_str, split):
    """Export markdown and return download path."""
    chunks_path = _config["chunks_path"]
    if not isinstance(chunks_path, Path) or not chunks_path.exists():
        return "No chunks file found. Run the pipeline first.", None

    exclude = ["structural", "empty"] if exclude_structural else ["empty"]
    inc_types = include_types if include_types else None
    chapters = None
    if chapters_str.strip():
        try:
            chapters = [int(x.strip()) for x in chapters_str.split(",")]
        except ValueError:
            return "Invalid chapter numbers. Use comma-separated integers.", None

    export_root = chunks_path.parent / "ui_exports" / uuid4().hex
    out_path = export_root / "textbook.md"
    chapters_dir = export_root / "Chapters"
    rag.export_markdown(
        chunks_path, out_path,
        include_types=inc_types,
        exclude_types=exclude,
        chapters=chapters,
        split_chapters=split,
        chapters_dir=chapters_dir,
    )

    if split:
        files = list(chapters_dir.glob("*.md"))
        if not files:
            return "No chapter files were produced for the selected filters.", None
        summary = f"Exported {len(files)} chapter files to `{chapters_dir}/`:\n"
        for f in sorted(files):
            summary += f"- {f.name} ({f.stat().st_size / 1024:.0f} KB)\n"
        archive = shutil.make_archive(
            str(export_root / "chapters"), "zip", root_dir=chapters_dir)
        return summary, archive

    if not out_path.is_file():
        return "No content was produced for the selected filters.", None
    size = out_path.stat().st_size / 1024
    return f"Exported to `{out_path}` ({size:.0f} KB)", str(out_path)


# ---------------------------------------------------------------------------
# Info tab
# ---------------------------------------------------------------------------

def do_info():
    """Return pipeline status as markdown."""
    lines = ["## Pipeline Status\n"]

    artifacts = [
        ("Chunks JSONL", _config["chunks_path"]),
        ("Vector DB", _config["db_path"]),
    ]
    for label, path in artifacts:
        if not isinstance(path, Path):
            lines.append(f"- **{label}**: not configured")
        elif path.exists():
            try:
                if path.is_dir():
                    size = sum(
                        f.stat().st_size
                        for f in path.rglob("*") if f.is_file())
                    lines.append(
                        f"- **{label}**: `{path}` ({size / 1e6:.1f} MB)")
                else:
                    lines.append(f"- **{label}**: `{path}` "
                                 f"({path.stat().st_size / 1e6:.1f} MB)")
            except OSError as exc:
                lines.append(f"- **{label}**: status unavailable ({exc})")
        else:
            lines.append(f"- **{label}**: not found")

    meta = _load_chunk_metadata()
    if meta["total"]:
        lines.append(f"\n### Chunks: {meta['total']}")
        lines.append(f"- **Content types**: {', '.join(meta['types'])}")
        lines.append(f"- **Chapters**: {', '.join(str(c) for c in meta['chapters'])}")

    # DB info
    db_path = _config["db_path"]
    collection = _config["collection"]
    if not isinstance(db_path, Path) or not collection:
        return "\n".join(lines)
    try:
        backend = _config["db_backend"]
        count = rag._index_collection_count(
            db_path, collection, db_backend=backend)
        lines.append("\n### Qdrant" if backend == "qdrant"
                     else "\n### ChromaDB")
        lines.append(f"- Collection: {collection}")
        lines.append(
            f"- {'Points' if backend == 'qdrant' else 'Documents'}: {count}")
    except LookupError:
        lines.append(f"\n### Vector DB: collection '{_config['collection']}' not found")
    except Exception as exc:
        lines.append(f"\n### Vector DB status unavailable: {exc}")

    lines.append("\n### Config")
    lines.append(f"- Embedding: `{_config['embedding_model']}`")
    lines.append(f"- Backend: {_config['db_backend']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_app():
    meta = _load_chunk_metadata()
    type_choices = ["All"] + meta["types"]
    ch_choices = ["All"] + [str(c) for c in meta["chapters"]]

    with gr.Blocks(title="RAG Pipeline", theme=gr.themes.Soft()) as app:
        gr.Markdown("# RAG Pipeline")

        with gr.Tab("Search"):
            with gr.Row():
                query_box = gr.Textbox(label="Query", placeholder="minimum contacts test...",
                                       scale=3)
                search_btn = gr.Button("Search", variant="primary", scale=1)

            with gr.Row():
                type_dd = gr.Dropdown(choices=type_choices, value="All",
                                      label="Content Type")
                ch_dd = gr.Dropdown(choices=ch_choices, value="All",
                                    label="Chapter")
                n_slider = gr.Slider(1, 20, value=5, step=1, label="Results")

            with gr.Row():
                hybrid_cb = gr.Radio(
                    choices=["Auto (recommended)", "Hybrid", "Vector only"],
                    value="Auto (recommended)", label="Retrieval mode")
                rerank_cb = gr.Radio(
                    choices=["Auto (recommended)", "Always", "Never"],
                    value="Auto (recommended)", label="Reranker mode")
                gr.Markdown(f"**Backend:** `{_config['db_backend']}`")

            results_md = gr.Markdown()

            search_btn.click(
                do_search,
                inputs=[query_box, type_dd, ch_dd, n_slider, hybrid_cb,
                        rerank_cb],
                outputs=results_md,
            )
            query_box.submit(
                do_search,
                inputs=[query_box, type_dd, ch_dd, n_slider, hybrid_cb,
                        rerank_cb],
                outputs=results_md,
            )

        with gr.Tab("Export"):
            gr.Markdown("### Export clean markdown for Claude Projects / NotebookLM")
            with gr.Row():
                exp_types = gr.CheckboxGroup(
                    choices=meta["types"], label="Include types (empty = all)")
                exp_exclude = gr.Checkbox(label="Exclude structural/empty",
                                          value=True)
            exp_chapters = gr.Textbox(
                label="Chapters (comma-separated, empty = all)",
                placeholder="2, 3, 14")
            exp_split = gr.Checkbox(label="Split into separate chapter files",
                                    value=False)
            exp_btn = gr.Button("Export", variant="primary")
            exp_result = gr.Markdown()
            exp_file = gr.File(label="Download", visible=True)

            exp_btn.click(do_export,
                          inputs=[exp_types, exp_exclude, exp_chapters, exp_split],
                          outputs=[exp_result, exp_file])

        with gr.Tab("Info"):
            info_md = gr.Markdown()
            refresh_btn = gr.Button("Refresh")
            refresh_btn.click(do_info, outputs=info_md)
            app.load(do_info, outputs=info_md)

    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RAG Pipeline Web UI")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--chunks", type=Path, required=True,
                        help="Book-scoped chunks JSONL from a pipeline run")
    parser.add_argument("--db", type=Path, required=True,
                        help="Book-scoped ChromaDB or Qdrant directory")
    parser.add_argument("--db-backend", type=str, default="chroma",
                        choices=["chroma", "qdrant"])
    parser.add_argument("--collection", type=str, required=True)
    parser.add_argument("--embedding-model", type=str,
                        default="nomic-ai/nomic-embed-text-v2-moe")
    parser.add_argument("--share", action="store_true",
                        help="Create a public Gradio share link")
    args = parser.parse_args()

    _config["chunks_path"] = args.chunks
    _config["db_backend"] = args.db_backend
    _config["db_path"] = args.db
    _config["collection"] = args.collection
    _config["embedding_model"] = args.embedding_model

    app = build_app()
    app.launch(server_port=args.port, share=args.share)


if __name__ == "__main__":
    main()
