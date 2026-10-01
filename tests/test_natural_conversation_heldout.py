"""Parent-selected held-out phrasing families, independent of parser implementation."""
from __future__ import annotations

import unittest

from academic_assistant.grounded_chat import GroundedChatEngine
from academic_assistant.models import AcademicChatRequest
from academic_assistant.transcript_assessment import TranscriptAssessor
from academic_assistant.transcript_models import TranscriptAssessmentRequest, TranscriptFollowupRequest


class NaturalConversationHeldOut(unittest.TestCase):
    def setUp(self):
        self.facade = GroundedChatEngine()

    def policy(self, question):
        response = self.facade.chat(AcademicChatRequest(question=question, admission_year=2026,
            matched_curriculum_year=2026, department="컴퓨터공학과", response_style="friendly", generate_answer=False))
        self.assertEqual("supported", response.status, question)
        self.assertEqual("supported", response.evidence_packet.status)
        self.assertEqual([ref.rule_id for ref in response.evidence_packet.applied_rules], response.presentation_claim_ids)
        self.assertIsNone(response.generated_answer)
        return response

    def test_course_question_frames_not_just_canonical_list_alias(self):
        for question in ("전공필수로 어떤 과목을 들어야 하나요?", "전공필수 과목으로 무엇을 들어야 하는지 알려주세요.",
                         "전필에는 어떤 과목들이 있나요?", "전공필수로 이수해야 하는 과목은 무엇인가요?"):
            with self.subTest(question=question):
                response = self.policy(question)
                self.assertEqual(["major.required-course-set"], response.intent_ids)
                for ref in response.evidence_packet.applied_rules:
                    outcome = self.facade.engine.registry.rules[ref.rule_id]["decision"]["outcome"]
                    for item in outcome.get("items", []):
                        self.assertIn(item["label"], response.conversational_answer)

    def test_zero_credit_mandatory_thesis_positive_frames(self):
        for question in ("졸업논문이 0학점이어도 반드시 이수해야 하나요?", "0학점 졸업논문도 꼭 이수해야 하나요?",
                         "졸업논문은 0학점인데 꼭 들어야 하나요?", "졸업논문이 0학점인 경우에도 이수해야 하나요?"):
            with self.subTest(question=question):
                response = self.policy(question)
                self.assertEqual(["graduation.thesis.completion-result"], response.intent_ids)
                self.assertIn("0학점", response.conversational_answer)
                self.assertIn("Fail", response.conversational_answer)

    def test_score_question_frames_not_just_current_trial_alias(self):
        for question in ("PCCP 합격 기준 점수는 몇 점인가요?", "피씨씨피 합격 점수는 몇 점 이상인가요?",
                         "현행 PCCP의 합격 기준 점수를 알려주세요.", "PCCP 합격하려면 점수가 얼마나 되어야 하나요?"):
            with self.subTest(question=question):
                response = self.policy(question)
                self.assertEqual(["operations.pccp-current-trial"], response.intent_ids)
                self.assertIn("400", response.conversational_answer)
                self.assertIn("시범", response.conversational_answer)

    def test_related_unsupported_conditions_are_not_discarded(self):
        for question in ("전공필수로 어떤 과목을 들어야 하고 장학금 신청은 언제인가요?",
                         "졸업논문이 0학점이면 제가 자동 면제되죠?",
                         "PCCP 합격 기준 점수는 2027학번도 400점인가요?",
                         "PCCP 점수만 넘으면 캡스톤도 자동 합격인가요?"):
            with self.subTest(question=question):
                response = self.facade.chat(AcademicChatRequest(question=question, admission_year=2026,
                    matched_curriculum_year=2026, department="컴퓨터공학과", response_style="friendly", generate_answer=False))
                self.assertNotEqual("supported", response.status)
                self.assertEqual([], response.presentation_claim_ids)
                self.assertIsNone(response.context_question)

    def test_personal_transcript_frames_use_only_confirmed_current_records(self):
        record = TranscriptAssessmentRequest(admission_year=2026, matched_curriculum_year=2026,
            department="컴퓨터공학과", degree_track="single_major", confirmed=True, record_complete=True,
            courses=[{"row_id": "synthetic-1", "course_name": "고급자료구조", "course_code": "CDA0143",
                      "credits": 3, "grade": "A0", "category": "major_required", "review_flags": []}])
        assessor = TranscriptAssessor(self.facade.engine)
        for question, check_id in (("내 성적표에서 남은 전공필수 과목은?", "major.required.course_set"),
                                   ("내 성적표에서 졸업까지 몇 학점 더 필요해?", "credits.graduation.total")):
            with self.subTest(question=question):
                response = assessor.followup(TranscriptFollowupRequest(question=question, transcript=record, response_style="friendly"))
                self.assertEqual("supported", response.status)
                self.assertEqual([check_id], response.focus_check_ids)
                self.assertEqual("supported", response.selected_checks[0].evidence_packet.status)
                self.assertIn("최종 졸업", response.conversational_answer)


if __name__ == "__main__":
    unittest.main()
