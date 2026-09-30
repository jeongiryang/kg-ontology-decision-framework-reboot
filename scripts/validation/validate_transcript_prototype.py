"""Validate new contracts against models AND canonical nested evidence semantics."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jsonschema import Draft202012Validator
from academic_assistant import transcript_models as models
from academic_assistant.core import AnswerEngine
from academic_assistant.transcript_assessment import TranscriptAssessor
from scripts.validation.validate_academic_knowledge import validate_evidence_packet


def main():
    root = Path(__file__).resolve().parents[2]
    names = {
        "TranscriptExtraction":"transcript-extraction", "TranscriptAssessmentRequest":"transcript-assessment-request",
        "TranscriptAssessmentResponse":"transcript-assessment-response", "TranscriptFollowupRequest":"transcript-followup-request",
        "TranscriptFollowupResponse":"transcript-followup-response",
    }
    validators = {}
    for name, filename in names.items():
        schema = json.loads((root / "contracts" / (filename + ".schema.json")).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        generated = getattr(models, name).model_json_schema()
        observed = {key:value for key,value in schema.items() if key not in {"$id","$schema"}}
        if generated != observed:
            raise ValueError("transcript model/schema mismatch: " + name)
        validators[name] = Draft202012Validator(schema)
    assessor = TranscriptAssessor(AnswerEngine())
    base = {"admission_year":2026,"matched_curriculum_year":2026,"department":"컴퓨터공학과","degree_track":"single_major","confirmed":True,"record_complete":True,"courses":[{"row_id":"synthetic-1","course_name":"가상 선택과목","credits":3,"grade":"A0","category":"free"}]}
    packet_count = 0
    for degree in ("single_major", "unknown", "multiple_major"):
        for complete in (True, False):
            payload = {**base,"degree_track":degree,"record_complete":complete}
            req = models.TranscriptAssessmentRequest.model_validate(payload)
            response = assessor.assess(req).model_dump(mode="json")
            validators["TranscriptAssessmentResponse"].validate(response)
            for check in response["checks"]:
                for packet in (check["evidence_packet"], check["policy_packet"]):
                    if packet is not None:
                        errors = validate_evidence_packet(root, packet)
                        if errors:
                            raise ValueError("canonical transcript evidence validation failed: " + "; ".join(errors))
                        packet_count += 1
    print(f"transcript-prototype: 5 contracts/model parity and {packet_count} canonical nested packets passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
