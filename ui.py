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
import tempfile
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
    "db_lock_timeout": rag.DEFAULT_DB_LOCK_TIMEOUT,
    "search_timeout": rag.DEFAULT_OPERATION_TIMEOUTS["query"],
    "info_timeout": rag.DEFAULT_OPERATION_TIMEOUTS["info"],
}

_VECTOR_WORKER_FLAG = "--vector-worker"


class _VectorWorkerError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        super().__init__(message)


def _execute_vector_request(request: dict) -> dict:
    """Execute one vector operation inside the isolated UI worker."""
    action = request.get("action")
    config = request["config"]
    if action == "search":
        options = request["options"]
        response = rag.search_index(
            request["query"], Path(config["db_path"]),
            db_backend=config["db_backend"],
            n_results=options["n_results"],
            content_type=options["content_type"],
            chapter_num=options["chapter_num"],
            collection_name=config["collection"],
            embedding_model=config["embedding_model"],
            use_reranker=options["use_reranker"],
            hybrid=options["hybrid"],
            chunks_path=Path(config["chunks_path"]),
            lock_timeout=config["db_lock_timeout"],
        )
        return {
            "hits": [
                {"text": hit.text, "metadata": hit.metadata,
                 "score": hit.score}
                for hit in response.hits
            ],
            "effective_mode": response.effective_mode,
            "reranker_applied": response.reranker_applied,
            "warnings": response.warnings,
        }
    if action == "info":
        return {"count": rag._index_collection_count(
            Path(config["db_path"]), config["collection"],
            db_backend=config["db_backend"],
            lock_timeout=config["db_lock_timeout"],
        )}
    raise ValueError(f"Unsupported UI vector action: {action!r}")


def _vector_worker_main(request_path: Path, result_path: Path) -> int:
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = {"ok": True, "result": _execute_vector_request(request)}
    except Exception as exc:
        result = {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    rag._atomic_write_json(result_path, result)
    return 0


def _supervised_vector_request(action: str, payload: dict, *,
                               timeout: float) -> dict:
    """Run a UI storage callback behind the CLI's hard process boundary."""
    request = {"action": action, **payload}
    with tempfile.TemporaryDirectory(prefix="rag-ui-vector-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        result_path = Path(temp_dir) / "result.json"
        rag._atomic_write_json(request_path, request)
        exit_code = rag._run_cli_with_deadline(
            Path(__file__),
            [_VECTOR_WORKER_FLAG, str(request_path), str(result_path)],
            operation=f"UI {action}", timeout=timeout)
        if exit_code == 124:
            raise TimeoutError(
                f"UI {action} exceeded its {timeout:g}s deadline")
        if exit_code:
            raise RuntimeError(
                f"UI {action} worker exited with status {exit_code}")
        try:
            envelope = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"UI {action} worker returned no valid result") from exc
        if not envelope.get("ok"):
            raise _VectorWorkerError(
                str(envelope.get("error_type", "RuntimeError")),
                str(envelope.get("message", "Vector operation failed")),
            )
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"UI {action} worker returned an invalid result")
        return result


def _vector_config_payload() -> dict:
    return {
        "db_path": str(_config["db_path"]),
        "chunks_path": str(_config["chunks_path"]),
        "db_backend": _config["db_backend"],
        "collection": _config["collection"],
        "embedding_model": _config["embedding_model"],
        "db_lock_timeout": _config["db_lock_timeout"],
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
        response = _supervised_vector_request(
            "search",
            {
                "query": query,
                "config": _vector_config_payload(),
                "options": {
                    "n_results": int(n_results),
                    "content_type": ct,
                    "chapter_num": ch,
                    "use_reranker": use_reranker,
                    "hybrid": hybrid,
                },
            },
            timeout=_config["search_timeout"],
        )

        elapsed = time.time() - t0
        mode = response["effective_mode"]
        if response["reranker_applied"]:
            mode += " + reranked"

        # Format output
        lines = [
            f"**{len(response['hits'])} results** ({mode}, {elapsed:.1f}s)\n"
        ]
        for warning in response["warnings"]:
            lines.append(f"> Warning: {warning}\n")

        for i, hit in enumerate(response["hits"]):
            doc = hit["text"]
            meta = hit["metadata"]
            ct_val = meta.get("content_type", "?")
            section = meta.get("section_path", "")
            case = meta.get("primary_case", "")
            pages = meta.get("page_range", "")
            ctx = meta.get("context", "")

            lines.append(f"### Result {i+1} (score: {hit['score']:.3f})")
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
        result = _supervised_vector_request(
            "info", {"config": _vector_config_payload()},
            timeout=_config["info_timeout"])
        count = result["count"]
        lines.append("\n### Qdrant" if backend == "qdrant"
                     else "\n### ChromaDB")
        lines.append(f"- Collection: {collection}")
        lines.append(
            f"- {'Points' if backend == 'qdrant' else 'Documents'}: {count}")
    except _VectorWorkerError as exc:
        if exc.error_type != "LookupError":
            lines.append(f"\n### Vector DB status unavailable: {exc}")
        else:
            lines.append(
                f"\n### Vector DB: collection '{_config['collection']}' not found")
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

def main(argv: list[str] | None = None):
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
    parser.add_argument(
        "--db-lock-timeout", type=float, default=rag.DEFAULT_DB_LOCK_TIMEOUT,
        help="Seconds to wait for local vector-store access")
    parser.add_argument(
        "--search-timeout", type=float,
        default=rag.DEFAULT_OPERATION_TIMEOUTS["query"],
        help="Hard wall-clock deadline for each isolated search")
    parser.add_argument(
        "--info-timeout", type=float,
        default=rag.DEFAULT_OPERATION_TIMEOUTS["info"],
        help="Hard wall-clock deadline for each isolated status lookup")
    parser.add_argument("--share", action="store_true",
                        help="Create a public Gradio share link")
    args = parser.parse_args(argv)
    try:
        args.db_lock_timeout = rag._normalize_db_lock_timeout(
            args.db_lock_timeout)
        args.search_timeout = rag._normalize_operation_timeout(
            args.search_timeout)
        args.info_timeout = rag._normalize_operation_timeout(
            args.info_timeout)
    except ValueError as exc:
        parser.error(str(exc))

    _config["chunks_path"] = args.chunks
    _config["db_backend"] = args.db_backend
    _config["db_path"] = args.db
    _config["collection"] = args.collection
    _config["embedding_model"] = args.embedding_model
    _config["db_lock_timeout"] = args.db_lock_timeout
    _config["search_timeout"] = args.search_timeout
    _config["info_timeout"] = args.info_timeout

    app = build_app()
    app.launch(server_port=args.port, share=args.share)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == _VECTOR_WORKER_FLAG:
        sys.exit(_vector_worker_main(Path(sys.argv[2]), Path(sys.argv[3])))
    main()
