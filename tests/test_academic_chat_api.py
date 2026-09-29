from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry as SchemaRegistry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.api import app
from academic_assistant.core import AnswerEngine
from academic_assistant.grounded_chat import GroundedChatEngine
from academic_assistant.registry import Registry


def payload(question: str, **updates: object) -> dict[str, object]:
    request: dict[str, object] = {
        "schema_version": "1.0.0",
        "question": question,
        "admission_year": 2026,
        "matched_curriculum_year": 2026,
        "department": "컴퓨터공학과",
        "earned_credits": {},
    }
    request.update(updates)
    return request


class _FakeSuggester:
    def __init__(self, intent_id: str = "credits.major.required") -> None:
        self.intent_id = intent_id
        self.calls = 0

    def suggest_intent(self, question: str, candidates: dict[str, str]) -> str:
        self.calls += 1
        return self.intent_id


class AcademicChatAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.registry = Registry.load(ROOT)
        cls.chat_schema = json.loads((ROOT / "contracts/academic-chat-response.schema.json").read_text(encoding="utf-8"))
        evidence_schema = json.loads((ROOT / "contracts/evidence-packet.schema.json").read_text(encoding="utf-8"))
        cls.schema_registry = SchemaRegistry().with_resource(evidence_schema["$id"], Resource.from_contents(evidence_schema))

    def test_supported_chat_preserves_deterministic_answer_and_citations(self) -> None:
        request = payload("졸업학점은 얼마인가요?")
        answer = self.client.post("/v1/academic/answers", json=request)
        chat = self.client.post("/v1/academic/chat", json=request)
        self.assertEqual(200, answer.status_code)
        self.assertEqual(200, chat.status_code)
        body = chat.json()
        self.assertEqual("supported", body["status"])
        self.assertEqual("skipped", body["llm_status"])
        self.assertNotIn("suggested_question", body)
        for key, value in answer.json().items():
            self.assertEqual(value, body[key], key)
        Draft202012Validator(self.chat_schema, registry=self.schema_registry).validate(body)

    def test_model_suggestion_cannot_promote_insufficient_evidence(self) -> None:
        fake = _FakeSuggester()
        facade = GroundedChatEngine(AnswerEngine(self.registry), fake)
        with patch("academic_assistant.api._chat_engine", return_value=facade):
            response = self.client.post("/v1/academic/chat", json=payload("휴학할 때 절차가 궁금합니다"))
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, fake.calls)
        self.assertEqual("insufficient_evidence", body["status"])
        self.assertEqual("suggested", body["llm_status"])
        self.assertTrue(body["suggested_question"])
        self.assertEqual([], body["evidence_packet"]["applied_rules"])
        self.assertEqual([], body["evidence_packet"]["evidence"])
        Draft202012Validator(self.chat_schema, registry=self.schema_registry).validate(body)

    def test_pending_pccp_and_other_department_never_reach_model(self) -> None:
        fake = _FakeSuggester()
        facade = GroundedChatEngine(AnswerEngine(self.registry), fake)
        with patch("academic_assistant.api._chat_engine", return_value=facade):
            pccp = self.client.post("/v1/academic/chat", json=payload("PCCP 400점이 캡스톤 합격 기준인가요?"))
            other = self.client.post("/v1/academic/chat", json=payload("전자공학과 졸업학점은 얼마인가요?"))
        self.assertEqual("insufficient_evidence", pccp.json()["status"])
        self.assertEqual("out_of_scope", other.json()["status"])
        self.assertEqual(0, fake.calls)

    def test_invalid_identifying_input_is_rejected_before_model(self) -> None:
        fake = _FakeSuggester()
        facade = GroundedChatEngine(AnswerEngine(self.registry), fake)
        with patch("academic_assistant.api._chat_engine", return_value=facade):
            response = self.client.post("/v1/academic/chat", json=payload("내 학번 2026123456 기준 졸업학점은?"))
        self.assertEqual(422, response.status_code)
        self.assertEqual(0, fake.calls)
        self.assertNotIn("2026123456", response.text)

    def test_cohort_question_feedback_follows_answer_safety_boundary(self) -> None:
        questions = ("2026학번 수강신청 일정은?", "26학번 수강신청 일정은?")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "feedback.jsonl"
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(destination),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(destination.parent),
            }):
                for question in questions:
                    with self.subTest(question=question):
                        answer = self.client.post("/v1/academic/answers", json=payload(question))
                        self.assertEqual(200, answer.status_code)
                        self.assertEqual("insufficient_evidence", answer.json()["status"])
                        feedback = {
                            "schema_version": "1.0.0",
                            "packet_id": answer.json()["packet_id"],
                            "status": "insufficient_evidence",
                            "question": question,
                            "earned_credits": {},
                            "category": "missing_evidence",
                            "consent_to_store": True,
                        }
                        accepted = self.client.post("/v1/academic/feedback", json=feedback)
                        self.assertEqual(201, accepted.status_code)
                        feedback["question"] = question.replace(" 수강신청", " 2026123456 수강신청")
                        rejected = self.client.post("/v1/academic/feedback", json=feedback)
                        self.assertEqual(422, rejected.status_code)
                        self.assertNotIn("2026123456", rejected.text)
            records = destination.read_text(encoding="utf-8").splitlines()
            self.assertEqual(2, len(records))
            self.assertTrue(all("question" not in json.loads(record) for record in records))
            for question in questions:
                self.assertTrue(all(question not in record for record in records))


if __name__ == "__main__":
    unittest.main()
