#!/usr/bin/env python3
"""Read-only JSON access to the existing authenticated loopback RAG service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, json.dumps({"schema_version": 1, "ok": False, "error": {
            "code": "invalid_arguments", "message": "Use --help and docs/ai-pipeline-access.md.",
            "retryable": False,
        }}) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(description=__doc__, prog="query_pipeline.py", allow_abbrev=False)
    parser.add_argument("--host", choices=("127.0.0.1", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=30.0, help="Overall network deadline, 0.1..600 seconds")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_ArgumentParser)
    health = commands.add_parser("health", allow_abbrev=False, help="Unauthenticated readiness or liveness")
    health.add_argument("--probe", choices=("live", "ready"), default="ready")
    commands.add_parser("schema", allow_abbrev=False, help="Validate and read the pinned service OpenAPI contract")
    commands.add_parser("corpora", allow_abbrev=False, help="List configured public corpus IDs")
    search = commands.add_parser("search", allow_abbrev=False, help="Read strict service search JSON from stdin (64 KiB maximum)")
    search.add_argument("--corpus", required=True, help="Opaque corpus ID returned by corpora; never a file path")
    evidence = commands.add_parser("search-evidence", allow_abbrev=False,
                                   help="Read opt-in source-scope evidence with search; OCR accuracy remains unverified")
    evidence.add_argument("--corpus", required=True, help="Configured opaque corpus ID; strict search JSON on stdin (64 KiB maximum)")
    try:
        args = parser.parse_args(argv)
        from ai_pipeline_client import PipelineClientError, PipelineReader, parse_search_json

        try:
            client = PipelineReader.from_environment(
                host=args.host, port=args.port, timeout=args.timeout,
                authenticated=args.command != "health")
            if args.command == "health":
                result = client.health(args.probe)
            elif args.command == "schema":
                result = client.schema()
            elif args.command == "corpora":
                result = client.corpora()
            else:
                if sys.stdin.isatty():
                    raise PipelineClientError("invalid_request")
                raw = sys.stdin.buffer.read(64 * 1024 + 1)
                payload = parse_search_json(raw)
                result = (client.search_evidence(args.corpus, payload) if args.command == "search-evidence"
                          else client.search(args.corpus, payload))
            print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True))
            return 3 if args.command == "health" and result["status"] == "not_ready" else 0
        except PipelineClientError as exc:
            print(json.dumps(exc.as_dict(), sort_keys=True), file=sys.stderr)
            return exc.exit_code
    except KeyboardInterrupt:
        print(json.dumps({"schema_version": 1, "ok": False, "error": {
            "code": "cancelled", "message": "The reader request was cancelled.", "retryable": False,
        }}), file=sys.stderr)
        return 130
    except (ImportError, OSError, ValueError, RuntimeError):
        print(json.dumps({"schema_version": 1, "ok": False, "error": {
            "code": "client_unavailable", "message": "The reader could not complete; see docs/ai-pipeline-access.md.",
            "retryable": False,
        }}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
