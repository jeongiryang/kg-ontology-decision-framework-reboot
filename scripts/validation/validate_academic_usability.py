#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.core import AnswerEngine
from academic_assistant.models import AcademicAnswerRequest

EXPECTED_COUNTS = {"supported": 22, "insufficient_evidence": 6, "out_of_scope": 2}


def validate(project: Path) -> list[str]:
    errors: list[str] = []
    payload = json.loads((project / "evaluations/academic-usability-pilot.json").read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if len(cases) != 30:
        errors.append(f"expected 30 usability cases, found {len(cases)}")
    ids = [case.get("id") for case in cases]
    if len(ids) != len(set(ids)):
        errors.append("usability case IDs must be unique")
    counts = Counter(case.get("expected_status") for case in cases)
    if dict(counts) != EXPECTED_COUNTS:
        errors.append(f"unexpected status distribution: {dict(counts)}")

    engine = AnswerEngine()
    default_scope = payload["scope"]
    for case in cases:
        case_id = case.get("id", "unknown")
        request = AcademicAnswerRequest(
            question=case["question"],
            admission_year=case.get("admission_year", default_scope["admission_year"]),
            matched_curriculum_year=case.get("matched_curriculum_year", default_scope["matched_curriculum_year"]),
            department=case.get("department", default_scope["department"]),
            earned_credits=case.get("earned_credits", {}),
        )
        try:
            response = engine.answer(request)
        except ValueError as exc:
            errors.append(f"{case_id}: request was rejected: {exc}")
            continue
        if response.status != case["expected_status"]:
            errors.append(f"{case_id}: expected {case['expected_status']}, got {response.status}")
            continue
        if "expected_intents" in case and response.intent_ids != case["expected_intents"]:
            errors.append(f"{case_id}: expected intents {case['expected_intents']}, got {response.intent_ids}")
        packet = response.evidence_packet
        if response.status == "supported":
            if not packet.applied_rules or not packet.evidence:
                errors.append(f"{case_id}: supported answer lacks approved rules or evidence")
        elif packet.applied_rules or packet.evidence:
            errors.append(f"{case_id}: unsupported answer exposed rules or evidence")
        expected_calculation = case.get("expected_calculation")
        if expected_calculation:
            matches = [item for item in response.calculations if item.metric == expected_calculation["metric"]]
            if len(matches) != 1 or matches[0].gap != expected_calculation["gap"]:
                errors.append(f"{case_id}: expected calculation {expected_calculation}, got {[item.model_dump() for item in response.calculations]}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = validate(args.project.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("academic usability pilot validation passed (30 cases: 22 supported, 6 insufficient_evidence, 2 out_of_scope)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
