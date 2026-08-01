#!/usr/bin/env python3
"""Internal telemetry worker used only by the hard-kill recovery drill."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import run_telemetry  # noqa: E402
import storage_policy  # noqa: E402


def main() -> None:
    events = Path(os.environ.pop("RAG_DRILL_EVENTS"))
    report = Path(os.environ.pop("RAG_DRILL_REPORT"))
    ready = Path(os.environ.pop("RAG_DRILL_READY"))
    telemetry = run_telemetry.RunTelemetry(
        "operational_drill",
        run_id=os.environ.pop("RAG_DRILL_RUN_ID"),
        events_path=events,
        report_path=report,
    )
    telemetry.start()
    telemetry.stage_started("hard_kill_work")
    storage_policy.atomic_write_private_text(ready, "ready\n")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
