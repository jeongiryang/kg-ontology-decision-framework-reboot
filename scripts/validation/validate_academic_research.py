#!/usr/bin/env python3
"""Validate that unverified academic research items remain isolated from answer evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


def _load(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path.as_posix()}: cannot load JSON: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path.as_posix()}: JSON root must be an object")
        return None
    return value


def validate_research(project: Path) -> list[str]:
    project = project.resolve()
    errors: list[str] = []
    schema = json.loads(
        (project / "contracts" / "academic-research-item.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    research_dir = project / "reviews" / "academic" / "research"
    research_files = sorted(research_dir.glob("*.json")) if research_dir.is_dir() else []
    if not research_files:
        return ["reviews/academic/research: at least one AcademicResearchItem JSON is required"]

    rule_values: dict[str, dict[str, Any]] = {}
    knowledge_values: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((project / "knowledge" / "rules").glob("*.json")):
        value = _load(path, errors)
        if value is not None:
            knowledge_values.append((path, value))
            if isinstance(value.get("rule_id"), str):
                rule_values[value["rule_id"]] = value
    for path in sorted((project / "knowledge" / "sources").glob("*.json")):
        value = _load(path, errors)
        if value is not None:
            knowledge_values.append((path, value))

    session_ids: set[str] = set()
    for path in sorted((project / "reviews" / "academic" / "clarifications").glob("*.json")):
        value = _load(path, errors)
        if value is not None and isinstance(value.get("session"), dict):
            session_id = value["session"].get("session_id")
            if isinstance(session_id, str):
                session_ids.add(session_id)

    seen_research_ids: set[str] = set()
    seen_claim_ids: set[str] = set()
    for path in research_files:
        relative = path.relative_to(project)
        item = _load(path, errors)
        if item is None:
            continue
        schema_errors = list(validator.iter_errors(item))
        for error in sorted(schema_errors, key=lambda entry: list(entry.path)):
            location = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f"{relative}:{location}: {error.message}")
        if schema_errors:
            continue

        research_id = item["research_id"]
        if research_id in seen_research_ids:
            errors.append(f"{relative}: duplicate research_id {research_id}")
        seen_research_ids.add(research_id)
        if item["review_session_id"] not in session_ids:
            errors.append(
                f"{relative}: unknown review_session_id {item['review_session_id']}"
            )
        for rule_id in item["linked_rule_ids"]:
            if rule_id not in rule_values:
                errors.append(f"{relative}: unknown linked RuleFact {rule_id}")

        forbidden_values = {research_id}
        for claim in item["claims"]:
            claim_id = claim["claim_id"]
            if claim_id in seen_claim_ids:
                errors.append(f"{relative}: duplicate claim_id {claim_id}")
            seen_claim_ids.add(claim_id)
            forbidden_values.update({claim_id, claim["statement"]})

        for knowledge_path, knowledge in knowledge_values:
            serialized = json.dumps(knowledge, ensure_ascii=False, sort_keys=True)
            for forbidden in forbidden_values:
                if forbidden in serialized:
                    errors.append(
                        f"{knowledge_path.relative_to(project)}: unverified research value "
                        f"{forbidden!r} must not appear in SourceEntry or RuleFact"
                    )

    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    errors = validate_research(args.project)
    if errors:
        for error in errors:
            print(f"academic-research: error: {error}", file=sys.stderr)
        return 1
    print("academic-research: unverified claims are isolated from answer evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
