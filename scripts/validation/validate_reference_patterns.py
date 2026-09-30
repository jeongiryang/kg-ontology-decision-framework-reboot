"""Fresh synthetic pattern regression, not a replay of reference UAT exports."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from academic_assistant.core import AnswerEngine, canonical_response_json
from academic_assistant.models import AcademicAnswerRequest
from academic_assistant.registry import Registry
from scripts.validation.validate_academic_knowledge import validate_evidence_packet


def validate(project: Path):
    payload = json.loads((project / "evaluations/academic-reference-patterns.json").read_text(encoding="utf-8"))
    errors = []
    cases = payload["cases"]
    if len(cases) != 15 or len({case["id"] for case in cases}) != 15 or payload["provenance"] != "new_synthetic_reference_patterns_not_transcript_replay":
        errors.append("invalid synthetic reference suite")
    engine = AnswerEngine(Registry.load(project))
    for case in cases:
        request = AcademicAnswerRequest(**payload["scope"], question=case["question"], earned_credits=case.get("earned_credits", {}))
        response = engine.answer(request)
        ids = [item.rule_id for item in response.evidence_packet.applied_rules]
        if response.status != case["expected_status"] or sorted(ids) != sorted(case["expected_rule_ids"]):
            errors.append(case["id"] + ": status/rules mismatch")
        if response.status == "supported":
            errors.extend(validate_evidence_packet(project, response.evidence_packet.model_dump(mode="json")))
        elif response.evidence_packet.evidence or ids:
            errors.append(case["id"] + ": unsupported evidence leakage")
        if "expected_gap" in case and (len(response.calculations) != 1 or response.calculations[0].gap != case["expected_gap"]):
            errors.append(case["id"] + ": gap mismatch")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = validate(args.project.resolve())
    print("\n".join(errors) if errors else "reference-patterns: 15 fresh synthetic cases passed; human UAT not run")
    raise SystemExit(bool(errors))
