#!/usr/bin/env python3
"""CLI for deterministic CompletionReport publication."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.reporting.reporting import ReportError, generate_public_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="project root")
    parser.add_argument("--input", type=Path, required=True, help="CompletionReport JSON")
    parser.add_argument(
        "--schema",
        type=Path,
        help="schema path; defaults to contracts/completion-report.schema.json",
    )
    parser.add_argument(
        "--reports-dir", type=Path, help="output root; defaults to <project>/reports"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = args.project.resolve()
    input_path = args.input if args.input.is_absolute() else project / args.input
    schema_path = args.schema if args.schema is not None else project / "contracts/completion-report.schema.json"
    if not schema_path.is_absolute():
        schema_path = project / schema_path
    reports_dir = args.reports_dir if args.reports_dir is not None else project / "reports"
    if not reports_dir.is_absolute():
        reports_dir = project / reports_dir

    try:
        report = json.loads(input_path.read_text(encoding="utf-8"))
        result = generate_public_report(report, reports_dir, schema_path=schema_path)
    except (OSError, json.JSONDecodeError, ReportError) as exc:
        print(f"completion-report: error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

