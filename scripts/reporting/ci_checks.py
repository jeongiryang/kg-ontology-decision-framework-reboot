#!/usr/bin/env python3
"""CI checks for public reports and harness documentation."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.reporting.reporting import (
    ReportError,
    assert_no_absolute_paths,
    assert_no_sensitive_data,
    publication_requires_pdf,
    render_markdown,
    render_pdf,
    validate_completion_report,
)

MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HARNESS_CHANGE_PREFIXES = (
    ".codex/agents/",
    ".agents/skills/",
    ".github/workflows/",
    "contracts/",
    "scripts/reporting/",
    "scripts/validation/",
)
HARNESS_CHANGE_FILES = {"AGENTS.md", ".codex/config.toml", "harness-manifest.yaml"}


def _schema_path(project: Path) -> Path | None:
    candidate = project / "contracts/completion-report.schema.json"
    return candidate if candidate.is_file() else None


def check_public_reports(project: Path) -> list[str]:
    errors: list[str] = []
    runs_dir = project / "reports/runs"
    pdf_dir = project / "reports/pdf"
    if not runs_dir.exists():
        return errors
    json_stems = {path.stem for path in runs_dir.glob("*.json")}
    markdown_stems = {path.stem for path in runs_dir.glob("*.md")}
    pdf_stems = {path.stem for path in pdf_dir.glob("*.pdf")} if pdf_dir.is_dir() else set()
    for orphan in sorted(markdown_stems - json_stems):
        errors.append(f"reports/runs/{orphan}.md: orphan Markdown without JSON source")
    for orphan in sorted(pdf_stems - json_stems):
        errors.append(f"reports/pdf/{orphan}.pdf: orphan PDF without JSON source")
    schema = _schema_path(project)
    for json_path in sorted(runs_dir.glob("*.json")):
        try:
            report = json.loads(json_path.read_text(encoding="utf-8"))
            validate_completion_report(report, schema_path=schema)
            assert_no_sensitive_data(report)
            assert_no_absolute_paths(report)
        except (OSError, json.JSONDecodeError, ReportError) as exc:
            errors.append(f"{json_path.relative_to(project)}: {exc}")
            continue
        run_id = report["run_id"]
        if json_path.stem != run_id:
            errors.append(f"{json_path.relative_to(project)}: filename must match run_id {run_id}")
        markdown_path = runs_dir / f"{run_id}.md"
        if not markdown_path.is_file():
            errors.append(f"{markdown_path.relative_to(project)}: missing Markdown pair")
        else:
            markdown_bytes = markdown_path.read_bytes()
            try:
                markdown = markdown_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                errors.append(f"{markdown_path.relative_to(project)}: invalid UTF-8: {exc}")
                markdown = ""
            try:
                assert_no_sensitive_data({"content": markdown})
                assert_no_absolute_paths({"content": markdown})
            except ReportError as exc:
                errors.append(f"{markdown_path.relative_to(project)}: {exc}")
            expected_markdown = render_markdown(report).encode("utf-8")
            if markdown_bytes != expected_markdown:
                errors.append(
                    f"{markdown_path.relative_to(project)}: does not byte-match regeneration from JSON"
                )
        pdf_path = pdf_dir / f"{run_id}.pdf"
        if publication_requires_pdf(report):
            if not pdf_path.is_file():
                errors.append(f"reports/pdf/{run_id}.pdf: required PDF is missing")
            else:
                try:
                    with tempfile.TemporaryDirectory(prefix="completion-report-ci-") as temp:
                        regenerated_pdf = Path(temp) / pdf_path.name
                        render_pdf(report, regenerated_pdf)
                        if pdf_path.read_bytes() != regenerated_pdf.read_bytes():
                            errors.append(
                                f"{pdf_path.relative_to(project)}: does not byte-match regeneration from JSON"
                            )
                except (OSError, ReportError) as exc:
                    errors.append(f"{pdf_path.relative_to(project)}: cannot regenerate PDF: {exc}")
        elif pdf_path.exists():
            errors.append(f"{pdf_path.relative_to(project)}: unexpected PDF for a non-PDF report")
    return errors


def _normalize_link_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    elif " " in target:
        target = target.split(" ", 1)[0]
    return target


def check_markdown(project: Path) -> list[str]:
    errors: list[str] = []
    candidates = set(project.glob("*.md"))
    for root in (project / "docs", project / ".agents/skills", project / "reports"):
        if root.is_dir():
            candidates.update(root.rglob("*.md"))
    for markdown_path in sorted(candidates):
        text = markdown_path.read_text(encoding="utf-8")
        open_fence: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if open_fence is None and stripped.startswith("```"):
                open_fence = "mermaid" if stripped == "```mermaid" else "other"
            elif open_fence is not None and stripped == "```":
                open_fence = None
        if open_fence is not None:
            errors.append(f"{markdown_path.relative_to(project)}: unclosed Mermaid block")
        for match in MARKDOWN_LINK_RE.finditer(text):
            target = _normalize_link_target(match.group(1))
            if not target or target.startswith(("#", "http://", "https://", "mailto:", "codex://")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            candidate = (markdown_path.parent / path_part).resolve()
            try:
                candidate.relative_to(project.resolve())
            except ValueError:
                errors.append(f"{markdown_path.relative_to(project)}: link escapes project: {target}")
                continue
            if not candidate.exists():
                errors.append(f"{markdown_path.relative_to(project)}: missing local link: {target}")
    return errors


def check_documentation_freshness(project: Path, changed_from: str | None) -> list[str]:
    if not changed_from:
        return []
    command = ["git", "diff", "--name-only", f"{changed_from}...HEAD"]
    completed = subprocess.run(command, cwd=project, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return [f"cannot inspect changed files from {changed_from}: {completed.stderr.strip()}"]
    changed = {line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()}
    harness_changed = any(
        path in HARNESS_CHANGE_FILES or path.startswith(HARNESS_CHANGE_PREFIXES)
        for path in changed
    )
    if not harness_changed:
        return []
    errors = []
    if not any(path.startswith("docs/harness/") and path.endswith(".md") for path in changed):
        errors.append("harness configuration changed without docs/harness Markdown changes")
    if not any(path.startswith("docs/harness/decisions/") and path.endswith(".md") for path in changed):
        errors.append("harness configuration changed without an ADR change")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--changed-from", help="Git base SHA/ref for documentation freshness")
    args = parser.parse_args(argv)
    project = args.project.resolve()
    errors = []
    errors.extend(check_public_reports(project))
    errors.extend(check_markdown(project))
    errors.extend(check_documentation_freshness(project, args.changed_from))
    if errors:
        for error in errors:
            print(f"ci-check: error: {error}", file=sys.stderr)
        return 1
    print("ci-check: public reports and harness documentation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
