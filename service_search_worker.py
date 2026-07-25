"""Hidden child-process composition root for one local service search."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import rag
import service_runtime


def _silence_worker_output() -> None:
    logging.disable(logging.CRITICAL)
    try:
        sink = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        return
    sys.stdout = sink
    sys.stderr = sink


def main(argv: Sequence[str] | None = None) -> int:
    """Run only the private search-worker action with two file paths."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (len(arguments) != 3
            or arguments[0] != service_runtime._SEARCH_WORKER_ACTION):
        return 2
    _silence_worker_output()
    return service_runtime.search_worker_main(
        Path(arguments[1]),
        Path(arguments[2]),
        search_index_fn=rag.search_index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
