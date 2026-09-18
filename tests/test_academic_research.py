from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validation.validate_academic_knowledge import validate_evidence_packet
from scripts.validation.validate_academic_research import validate_research


ROOT = Path(__file__).resolve().parents[1]


class AcademicResearchIsolationTest(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        for directory in ("contracts", "knowledge", "reviews"):
            shutil.copytree(ROOT / directory, root / directory)

    def test_repository_research_items_are_isolated(self) -> None:
        self.assertEqual(validate_research(ROOT), [])
        path = ROOT / "reviews/academic/research/cwnu.cs.2026.graduation-practices.json"
        item = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(item["claims"]), 3)
        self.assertEqual(
            [claim["statement"] for claim in item["claims"]],
            [
                "총장상급 이상 외부 공모전 수상이 졸업작품 대체요건일 가능성이 있다.",
                "캡스톤디자인 1 통과, 캡스톤디자인 2 통과, 졸업작품 순서의 선후관계가 있을 가능성이 있다.",
                "3학년 2학기 성적 산출 전 PCCP 400점이 캡스톤디자인 1 통과 조건일 가능성이 있다.",
            ],
        )
        self.assertFalse(item["eligible_for_academic_answer"])
        self.assertEqual(item["status"], "awaiting_official_source")

    def test_unverified_claim_cannot_be_copied_into_rule_fact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            item_path = next((project / "reviews/academic/research").glob("*.json"))
            item = json.loads(item_path.read_text(encoding="utf-8"))
            rule_path = next((project / "knowledge/rules").glob("*.json"))
            rule = json.loads(rule_path.read_text(encoding="utf-8"))
            rule["review_notes"] = [item["claims"][0]["statement"]]
            rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            errors = validate_research(project)
            self.assertTrue(any("must not appear" in error for error in errors))

    def test_unknown_linked_rule_and_session_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            item_path = next((project / "reviews/academic/research").glob("*.json"))
            item = json.loads(item_path.read_text(encoding="utf-8"))
            item["linked_rule_ids"] = ["missing.rule"]
            item["review_session_id"] = "missing.session"
            item_path.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
            errors = validate_research(project)
            self.assertTrue(any("unknown linked RuleFact" in error for error in errors))
            self.assertTrue(any("unknown review_session_id" in error for error in errors))

    def test_unverified_question_returns_insufficient_evidence(self) -> None:
        packet = {
            "schema_version": "2.0.0",
            "packet_id": "graduation.practice.pending",
            "scope": {
                "admission_year": 2026,
                "matched_curriculum_year": 2026,
                "department": "컴퓨터공학과",
            },
            "student_facts": {},
            "applied_rules": [],
            "evidence": [],
            "issues": [{
                "kind": "missing",
                "message": "공모전·캡스톤·PCCP 운영요건은 공식 근거 조사 대기 상태다.",
                "related_ids": ["cwnu.cs.2026.graduation-practices"],
            }],
            "status": "insufficient_evidence",
        }
        self.assertEqual(validate_evidence_packet(ROOT, packet), [])

    def test_unverified_claim_cannot_be_used_as_supported_evidence(self) -> None:
        item_path = ROOT / "reviews/academic/research/cwnu.cs.2026.graduation-practices.json"
        item = json.loads(item_path.read_text(encoding="utf-8"))
        claim = item["claims"][0]
        packet = {
            "schema_version": "2.0.0",
            "packet_id": "graduation.practice.unsafely-supported",
            "scope": {
                "admission_year": 2026,
                "matched_curriculum_year": 2026,
                "department": "컴퓨터공학과",
            },
            "student_facts": {},
            "applied_rules": [{"rule_id": claim["claim_id"], "rule_sha256": "a" * 64}],
            "evidence": [{
                "source_id": item["research_id"],
                "rule_id": claim["claim_id"],
                "locator": item["github_issue_url"],
                "claim": claim["statement"],
            }],
            "issues": [],
            "status": "supported",
        }
        errors = validate_evidence_packet(ROOT, packet)
        self.assertTrue(any("unverified research" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
