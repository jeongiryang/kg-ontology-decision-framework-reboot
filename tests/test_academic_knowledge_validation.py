from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validation.validate_academic_knowledge import (
    canonical_sha256,
    validate_evidence_packet,
    validate_knowledge,
)


ROOT = Path(__file__).resolve().parents[1]


class AcademicKnowledgeValidationTest(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        shutil.copytree(ROOT / "contracts", root / "contracts")
        shutil.copytree(ROOT / "knowledge", root / "knowledge")
        shutil.copytree(ROOT / "reviews", root / "reviews")

    def test_repository_knowledge_is_consistent(self) -> None:
        self.assertEqual(validate_knowledge(ROOT), [])

    def test_every_source_and_rule_requires_a_review_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            review_path = next((project / "reviews/academic").glob("*.json"))
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["subjects"].pop()
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            errors = validate_knowledge(project)
            self.assertTrue(any("missing review subject" in error for error in errors))

    def test_approved_rule_requires_authoritative_approved_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            rule_path = next((project / "knowledge/rules").glob("*.json"))
            rule = json.loads(rule_path.read_text(encoding="utf-8"))
            rule["review"] = {
                "status": "approved",
                "mode": "human",
                "scope": "full",
                "reviewer_id": "test_reviewer",
                "reviewed_at": "2026-09-18T12:00:00+09:00",
                "rationale": "test",
            }
            rule["evidence"][0]["evidence_type"] = "interpretation"
            rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            errors = validate_knowledge(project)
            self.assertTrue(any("requires approved authoritative evidence" in error for error in errors))

    def test_review_packet_cannot_be_approved_with_pending_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            review_path = next((project / "reviews/academic").glob("*.json"))
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["overall_status"] = "approved"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            errors = validate_knowledge(project)
            self.assertTrue(any("contains non-approved subjects" in error for error in errors))

    def test_review_queue_and_object_status_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            review_path = next((project / "reviews/academic").glob("*.json"))
            review = json.loads(review_path.read_text(encoding="utf-8"))
            for subject in review["subjects"]:
                subject.update(
                    status="approved",
                    reviewer_id="test_reviewer",
                    reviewed_at="2026-09-18T12:00:00+09:00",
                    comment="approved in queue only",
                )
            review["overall_status"] = "approved"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            errors = validate_knowledge(project)
            self.assertTrue(any("but object review is" in error for error in errors))

    def test_supported_packet_rejects_unknown_rule_and_source(self) -> None:
        packet = {
            "schema_version": "2.0.0",
            "packet_id": "fake.supported.packet",
            "scope": {
                "admission_year": 2026,
                "matched_curriculum_year": 2026,
                "department": "컴퓨터공학과",
            },
            "student_facts": {},
            "applied_rules": [{"rule_id": "missing.rule", "rule_sha256": "a" * 64}],
            "evidence": [
                {
                    "source_id": "missing.source",
                    "rule_id": "missing.rule",
                    "locator": "invented",
                    "claim": "invented",
                }
            ],
            "issues": [],
            "status": "supported",
        }
        errors = validate_evidence_packet(ROOT, packet)
        self.assertTrue(any("unknown applied rule" in error for error in errors))

    def test_supported_packet_requires_current_approved_hash_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            reviewer = "test_reviewer"
            reviewed_at = "2026-09-18T12:00:00+09:00"
            rationale = "full source and rule review completed"
            for directory in ("sources", "rules"):
                for path in (project / "knowledge" / directory).glob("*.json"):
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["review"] = {
                        "status": "approved",
                        "mode": "human",
                        "scope": "full",
                        "reviewer_id": reviewer,
                        "reviewed_at": reviewed_at,
                        "rationale": rationale,
                    }
                    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            review_path = next((project / "reviews/academic").glob("*.json"))
            review = json.loads(review_path.read_text(encoding="utf-8"))
            for subject in review["subjects"]:
                subject.update(
                    status="approved",
                    reviewer_id=reviewer,
                    reviewed_at=reviewed_at,
                    comment=rationale,
                )
            review["overall_status"] = "approved"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

            rule_path = next((project / "knowledge/rules").glob("*.json"))
            rule = json.loads(rule_path.read_text(encoding="utf-8"))
            source_evidence = rule["evidence"][0]
            packet = {
                "schema_version": "2.0.0",
                "packet_id": "approved.supported.packet",
                "scope": {
                    "admission_year": 2026,
                    "matched_curriculum_year": 2026,
                    "department": "컴퓨터공학과",
                },
                "student_facts": {},
                "applied_rules": [
                    {"rule_id": rule["rule_id"], "rule_sha256": canonical_sha256(rule)}
                ],
                "evidence": [
                    {
                        "source_id": source_evidence["source_id"],
                        "rule_id": rule["rule_id"],
                        "locator": source_evidence["locator"],
                        "claim": rule["decision"]["statement"],
                    }
                ],
                "issues": [],
                "status": "supported",
            }
            self.assertEqual(validate_evidence_packet(project, packet), [])

            packet["evidence"][0]["claim"] = "완전히 무관한 주장"
            errors = validate_evidence_packet(project, packet)
            self.assertTrue(any("claim does not match" in error for error in errors))
            packet["evidence"][0]["claim"] = rule["decision"]["statement"]

            second_rule_path = next(
                path for path in (project / "knowledge/rules").glob("*.json") if path != rule_path
            )
            second_rule = json.loads(second_rule_path.read_text(encoding="utf-8"))
            packet["applied_rules"].append(
                {
                    "rule_id": second_rule["rule_id"],
                    "rule_sha256": canonical_sha256(second_rule),
                }
            )
            errors = validate_evidence_packet(project, packet)
            self.assertTrue(any("has no registered citation" in error for error in errors))
            packet["applied_rules"].pop()

            review["overall_status"] = "open"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            errors = validate_evidence_packet(project, packet)
            self.assertTrue(any("overall_status is not approved" in error for error in errors))
            review["overall_status"] = "approved"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

            packet["applied_rules"][0]["rule_sha256"] = "b" * 64
            packet["scope"]["admission_year"] = 2025
            errors = validate_evidence_packet(project, packet)
            self.assertTrue(any("canonical hash mismatch" in error for error in errors))
            self.assertTrue(any("admission year is outside" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
