#!/usr/bin/env python3
"""Extract and render every Mermaid block under docs/harness with Mermaid CLI."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


START = re.compile(r"^\s*```mermaid\s*$")
END = re.compile(r"^\s*```\s*$")


def extract_blocks(project: Path) -> list[tuple[Path, int, str]]:
    blocks: list[tuple[Path, int, str]] = []
    docs_root = project / "docs/harness"
    for markdown in sorted(docs_root.rglob("*.md")):
        lines = markdown.read_text(encoding="utf-8").splitlines()
        collecting = False
        content: list[str] = []
        start_line = 0
        for line_number, line in enumerate(lines, start=1):
            if not collecting and START.match(line):
                collecting = True
                content = []
                start_line = line_number
            elif collecting and END.match(line):
                blocks.append((markdown, start_line, "\n".join(content) + "\n"))
                collecting = False
            elif collecting:
                content.append(line)
        if collecting:
            raise ValueError(f"{markdown.relative_to(project)}:{start_line}: unclosed Mermaid block")
    return blocks


def render(project: Path, output_dir: Path, mmdc: str) -> int:
    blocks = extract_blocks(project)
    if not blocks:
        print("mermaid-render: error: no Mermaid blocks found under docs/harness", file=sys.stderr)
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, (markdown, line, content) in enumerate(blocks, start=1):
        stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", markdown.relative_to(project).as_posix())
        source = output_dir / f"{index:03d}-{stem}-L{line}.mmd"
        target = source.with_suffix(".svg")
        source.write_text(content, encoding="utf-8")
        completed = subprocess.run(
            [mmdc, "-i", str(source), "-o", str(target)],
            cwd=project,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            print(
                f"mermaid-render: error: {markdown.relative_to(project)}:{line}: {detail}",
                file=sys.stderr,
            )
            return 1
        if not target.is_file() or target.stat().st_size == 0:
            print(
                f"mermaid-render: error: {markdown.relative_to(project)}:{line}: empty SVG output",
                file=sys.stderr,
            )
            return 1
    print(f"mermaid-render: rendered {len(blocks)} block(s) with {mmdc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mmdc", default="mmdc")
    args = parser.parse_args(argv)
    project = args.project.resolve()
    output = args.output_dir.resolve()
    try:
        return render(project, output, args.mmdc)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"mermaid-render: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

