from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator


EXPECTED = {"canonical":13,"paraphrase":13,"credit-gap":11,"unverified-practice":3,"scope-unsupported":4,"ambiguity-conflict":2,"privacy-invalid":2}


def validate(project: Path) -> list[str]:
    errors: list[str] = []
    evaluation = json.loads((project / "evaluations/academic-answer-mvp.json").read_text(encoding="utf-8"))
    cases = evaluation.get("cases", [])
    if len(cases) != 48:
        errors.append(f"evaluation case count is {len(cases)}, expected 48")
    if Counter(case.get("category") for case in cases) != Counter(EXPECTED):
        errors.append("evaluation category counts do not match the contract")
    if len({case.get("id") for case in cases}) != len(cases):
        errors.append("evaluation case ids are not unique")
    for name in ("academic-answer-request.schema.json", "academic-answer-response.schema.json"):
        schema = json.loads((project / "contracts" / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    sys.path.insert(0, str(project / "src"))
    from academic_assistant.core import AnswerEngine
    from academic_assistant.models import AcademicAnswerRequest
    from academic_assistant.registry import Registry
    engine = AnswerEngine(Registry.load(project))
    defaults = evaluation["scope"]
    for case in cases:
        payload = {
            "question": case["question"],
            "admission_year": case.get("admission_year", defaults["admission_year"]),
            "matched_curriculum_year": case.get("matched_curriculum_year", defaults["matched_curriculum_year"]),
            "department": case.get("department", defaults["department"]),
            "earned_credits": case.get("earned_credits", {}),
        }
        try:
            result = engine.answer(AcademicAnswerRequest.model_validate(payload))
            if case.get("expected_error"):
                errors.append(f"{case['id']}: expected invalid request")
                continue
            if result.status != case["expected_status"]:
                errors.append(f"{case['id']}: got {result.status}, expected {case['expected_status']}")
            if "expected_intents" in case and sorted(result.intent_ids) != sorted(case["expected_intents"]):
                errors.append(f"{case['id']}: intent mismatch")
            if "expected_gap" in case and (not result.calculations or result.calculations[0].gap != case["expected_gap"]):
                errors.append(f"{case['id']}: gap mismatch")
        except (ValueError, TypeError):
            if not case.get("expected_error"):
                errors.append(f"{case['id']}: unexpected invalid request")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path("."))
    args = parser.parse_args()
    errors = validate(args.project.resolve())
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("academic answer engine validation passed (48 cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
