"""Independent natural-conversation QA: approved facts, no live inference/services."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError as SchemaError
from pydantic import ValidationError
from referencing import Registry as SchemaRegistry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant import llm
from academic_assistant.api import app
from academic_assistant.core import AnswerEngine
from academic_assistant.grounded_chat import GroundedChatEngine, GroundedChatResponse
from academic_assistant.llm import LLMSettings, LocalLLMClient
from academic_assistant.models import AcademicAnswerRequest, AcademicChatRequest
from academic_assistant.registry import Registry
from academic_assistant.transcript_assessment import TranscriptAssessor
from academic_assistant.transcript_models import (
    TranscriptAssessmentRequest, TranscriptFollowupRequest, TranscriptFollowupResponse,
)


def request(question="졸업 총학점 기준", **updates):
    value = dict(question=question, admission_year=2026, matched_curriculum_year=2026,
                 department="컴퓨터공학과", earned_credits={}, response_style="friendly",
                 generate_answer=False)
    value.update(updates)
    return AcademicChatRequest.model_validate(value)


def row(index=1, *, credits=3, grade="A0", category="free", **updates):
    value = dict(row_id=f"sample-{index}", course_name=f"가상과목{index}",
                 credits=credits, grade=grade, category=category)
    value.update(updates)
    return value


def transcript(courses=None, **updates):
    value = dict(admission_year=2026, matched_curriculum_year=2026,
                 department="컴퓨터공학과", degree_track="single_major", confirmed=True,
                 record_complete=True, courses=courses or [row()])
    value.update(updates)
    return TranscriptAssessmentRequest.model_validate(value)


class NoInference:
    """A swallowed provider exception must not hide a forbidden call."""
    def __init__(self):
        self.calls = []

    def suggest_intent(self, *args):
        self.calls.append(("intent", args))
        raise AssertionError("Initial friendly answer attempted intent inference")

    def generate_grounded(self, plan):
        self.calls.append(("generation", deepcopy(plan)))
        raise AssertionError("Initial friendly answer attempted answer inference")


class PublicWire:
    """Capture the actual provider request; return only synthetic approved claims."""
    def __init__(self):
        self.calls = []

    def __call__(self, transport_request):
        payload = json.loads(transport_request.data)
        self.calls.append(deepcopy(payload))
        plan = json.loads(payload["prompt"])["plan"]
        document = {
            "basis_sha256": plan["basis_sha256"],
            "introduction": "확인된 기준을 안내해 드리겠습니다.",
            "sentences": [{"claim_id": item["claim_id"], "text": item["statement"]}
                          for item in plan["claims"]],
        }
        return json.dumps({"response": json.dumps(document, ensure_ascii=False)},
                          ensure_ascii=False).encode("utf-8")


class NaturalConversationIndependentQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gold = json.loads((ROOT / "tests/fixtures/natural-conversation-evaluation.json").read_text(encoding="utf-8"))
        cls.registry = Registry.load(ROOT)
        cls.engine = AnswerEngine(cls.registry)
        cls.entries = {entry["intent_id"]: entry for entry in cls.registry.intents["intents"]}
        evidence = json.loads((ROOT / "contracts/evidence-packet.schema.json").read_text(encoding="utf-8"))
        resources = SchemaRegistry().with_resource(evidence["$id"], Resource.from_contents(evidence))
        cls.validators = {}
        for name in ("academic-chat-request", "academic-chat-response",
                     "transcript-followup-request", "transcript-followup-response"):
            schema = json.loads((ROOT / f"contracts/{name}.schema.json").read_text(encoding="utf-8"))
            cls.validators[name] = Draft202012Validator(schema, registry=resources)

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ACADEMIC_LLM_PROVIDER": "disabled",
                                "ACADEMIC_LLM_GROUNDED_GENERATION": "1"}).start()
        patch("academic_assistant.llm._budget", llm._RequestBudget()).start()
        self.no_model = NoInference()
        self.facade = GroundedChatEngine(self.engine, self.no_model)

    def answer(self, question, **updates):
        result = self.facade.chat(request(question, **updates))
        self.assertEqual([], self.no_model.calls)
        self.validators["academic-chat-response"].validate(result.model_dump(mode="json", exclude_none=True))
        return result

    def assert_supported(self, result, expected_intents):
        self.assertEqual("supported", result.status)
        self.assertEqual(set(expected_intents), set(result.intent_ids))
        packet = result.evidence_packet
        self.assertEqual("supported", packet.status)
        expected_rules = {rule for intent in expected_intents for rule in self.entries[intent]["rule_ids"]}
        self.assertEqual(expected_rules, {item.rule_id for item in packet.applied_rules})
        self.assertEqual([item.rule_id for item in packet.applied_rules], result.presentation_claim_ids)
        self.assertTrue(result.conversational_answer)
        self.assertTrue(result.context_question)
        self.assertLessEqual(len(result.context_question), 500)
        self.assertFalse(packet.issues)
        self.assertEqual(2026, packet.scope.admission_year)
        self.assertEqual(2026, packet.scope.matched_curriculum_year)
        self.assertEqual("컴퓨터공학과", packet.scope.department)
        for applied in packet.applied_rules:
            self.assertEqual(self.registry.rule_hashes[applied.rule_id], applied.rule_sha256)
            rule = self.registry.rules[applied.rule_id]
            self.assertEqual(("human", "full", "approved"),
                             tuple(rule["review"][key] for key in ("mode", "scope", "status")))
            references = [ref for ref in packet.evidence if ref.rule_id == applied.rule_id]
            self.assertTrue(references, applied.rule_id)
            for ref in references:
                self.assertIn(ref.source_id, self.registry.sources)
                if ref.source_id == "cwnu.cs.2026.department-confirmation-20261001":
                    self.assertNotIn("PDF", ref.locator, "A human confirmation is not a PDF citation")
                else:
                    self.assertIn("PDF", ref.locator)
                self.assertIn((ref.source_id, ref.locator),
                              {(item["source_id"], item["locator"]) for item in rule["evidence"]})
                self.assertEqual(rule["decision"]["statement"], ref.claim)
        prose = result.conversational_answer
        self.assertNotRegex(prose, r"credits\.|cwnu\.|student\.|C:\\|/home/|ssh://")
        for rule_id in expected_rules:
            outcome = self.registry.rules[rule_id]["decision"]["outcome"]
            if outcome["type"] == "credit_threshold":
                self.assertRegex(prose, rf"{outcome['credits']}\s*학점")
                self.assertRegex(prose, r"최소|이상")
            elif outcome["type"] in {"coverage_requirement", "recommendation"}:
                normalized_prose = re.sub(r"[\s·]", "", prose)
                for item in outcome["items"]:
                    self.assertIn(re.sub(r"[\s·]", "", item["label"]), normalized_prose)
    def assert_refused(self, result, status):
        self.assertEqual(status, result.status)
        self.assertEqual(status, result.evidence_packet.status)
        self.assertEqual([], result.evidence_packet.applied_rules)
        self.assertEqual([], result.evidence_packet.evidence)
        self.assertEqual([], result.presentation_claim_ids)
        self.assertIsNone(result.context_question)
        self.assertTrue(result.conversational_answer)
        self.assertNotIn("130학점", result.conversational_answer)

    def test_gold_is_catalog_complete_independent_and_beyond_demo_menu(self):
        topics = self.gold["topics"]
        self.assertEqual(31, len(topics))
        self.assertEqual(set(self.entries), {case["intent_id"] for case in topics})
        questions = [question for case in topics for question in case["questions"]]
        self.assertEqual(62, len(questions))
        self.assertEqual(len(questions), len(set(questions)))
        self.assertTrue(all(len(case["questions"]) >= 2 for case in topics))
        self.assertEqual(6, len(self.gold["combinations"]))
        self.assertGreaterEqual(len(self.gold["refusals"]), 15)

    def test_all31_topics_with62_independently_written_natural_questions(self):
        with patch("academic_assistant.api._chat_engine", return_value=self.facade), TestClient(app) as client:
            for case in self.gold["topics"]:
                for question in case["questions"]:
                    with self.subTest(intent=case["intent_id"], question=question):
                        payload = request(question).model_dump(mode="json")
                        self.validators["academic-chat-request"].validate(payload)
                        response = client.post("/v1/academic/chat", json=payload)
                        self.assertEqual(200, response.status_code)
                        data = response.json()
                        self.validators["academic-chat-response"].validate(data)
                        self.assert_supported(GroundedChatResponse.model_validate(data), [case["intent_id"]])
                        self.assertEqual([], self.no_model.calls)

    def test_complete_compound_queries_cover_every_claim_without_partial_answer(self):
        for case in self.gold["combinations"]:
            with self.subTest(question=case["question"]):
                self.assert_supported(self.answer(case["question"]), case["intent_ids"])

    def test_safety_guards_survive_friendly_rewriting_and_previous_anchor(self):
        for case in self.gold["refusals"]:
            for previous in (None, "졸업 총학점 기준"):
                with self.subTest(question=case["question"], previous=previous):
                    self.assert_refused(self.answer(case["question"], previous_question=previous), case["status"])

    def test_first_friendly_unmatched_turn_invokes_neither_model_path(self):
        for question in ("학사 이수 내용이 궁금합니다", "학점 관련해서 질문이 있어요", "그럼 몇 학점 남았어?"):
            result = self.answer(question)
            self.assertNotEqual("supported", result.status)
            self.assertIsNone(result.generated_answer)
            self.assertEqual([], self.no_model.calls)

    def test_supported_anchor_chains_replay_current_credits_and_preserve_units(self):
        first = self.answer("졸업에 필요한 전체 학점을 알려주실래요?")
        self.assert_supported(first, ["credits.graduation.total"])
        anchor = first.context_question
        for earned, gap in ((100, 30), (125, 5), (130, 0), (150, 0)):
            second = self.answer("그럼 몇 학점 남았어?", previous_question=anchor,
                                 earned_credits={"credits.graduation.total": earned})
            self.assert_supported(second, ["credits.graduation.total"])
            self.assertTrue(second.context_used)
            self.assertEqual(gap, second.calculations[0].gap)
            self.assertEqual({"credits.graduation.total": earned}, second.evidence_packet.student_facts)
            third = self.answer("그럼 다시 설명해 주세요", previous_question=second.context_question,
                                earned_credits={"credits.graduation.total": earned})
            self.assert_supported(third, ["credits.graduation.total"])
        for question in ("그럼 몇 과목인가요?", "그럼 몇 번인가요?", "그 과목은 뭐야?"):
            result = self.answer(question, previous_question=anchor)
            self.assertNotEqual("supported", result.status)
            self.assertIsNone(result.context_question)
        no_facts = self.answer("그럼 몇 학점 남았어?", previous_question=anchor)
        self.assertEqual("insufficient_evidence", no_facts.status)
        self.assertFalse(no_facts.calculations)
        self.assertFalse(no_facts.evidence_packet.evidence)

    def test_bundle_explanation_is_supported_but_numeric_followup_is_ambiguous(self):
        for question, intent in (("교양 학점 기준", "credits.general.bundle"),
                                 ("전공 학점 기준", "credits.major.bundle")):
            initial = self.answer(question)
            self.assert_supported(initial, [intent])
            explain = self.answer("다시 설명해 주세요", previous_question=initial.context_question)
            self.assert_supported(explain, [intent])
            count = self.answer("그럼 몇 학점인가요?", previous_question=initial.context_question)
            self.assertEqual("insufficient_evidence", count.status)
            self.assertIsNone(count.context_question)
            self.assertLessEqual(len(count.clarification_choices), 3)
            self.assertTrue(count.clarification_choices)
            for choice in count.clarification_choices:
                candidate = self.answer(choice.question)
                self.assertEqual("supported", candidate.status)

    def test_compatible_course_count_counseling_count_and_thesis_explanation(self):
        cases = (("전공필수 과목 목록", "그럼 몇 과목인가요?", "major.required-course-set"),
                 ("심층상담 횟수 기준", "그럼 몇 번인가요?", "major.counseling-completion"),
                 ("졸업논문 필수인가요?", "다시 설명해 주세요", "graduation.thesis.required"))
        for initial_question, followup, intent in cases:
            initial = self.answer(initial_question)
            self.assert_supported(initial, [intent])
            result = self.answer(followup, previous_question=initial.context_question)
            self.assert_supported(result, [intent])
            self.assertTrue(result.context_used)

    def test_current_scope_and_conflicts_cannot_borrow_a_previous_approved_anchor(self):
        for updates in ({"admission_year": 2025}, {"matched_curriculum_year": 2025}, {"department": "간호학과"}):
            self.assert_refused(self.answer("그럼 몇 학점 남았어?", previous_question="졸업 총학점 기준", **updates), "out_of_scope")
        changed = replace(self.registry, conflicts={"credit_threshold:credits.graduation.total": ["left", "right"]})
        result = GroundedChatEngine(AnswerEngine(changed), self.no_model).chat(
            request("그럼 몇 학점 남았어?", previous_question="졸업 총학점 기준"))
        self.assert_refused(result, "conflict")

    def test_friendly_claims_retain_operational_and_exception_caveats(self):
        required = {
            "현행 PCCP 합격 기준": (r"400", r"(?:잠정|현행|현재|시행)", r"(?:확정|보장|추후|변경)"),
            "코딩 테스트 미통과 조치": (r"캡스톤디자인 I.*U", r"다음 연도.*캡스톤디자인 II.*(?:수강할 수 없|수강 불가)", r"캡스톤 I.*금지.*(?:아니|아닌)"),
            "졸업작품 수강 선행조건": (r"캡스톤", r"(?:PASS|Pass|pass|통과)", r"(?:자동|충분|졸업)"),
            "졸업논문 대체 요건": (r"(?:명시|안내|자료)", r"(?:없|않)", r"(?:금지|승인|판정|단정|확정)"),
            "학석사 연계과정 논문 면제": (r"면제", r"(?:자동|개인|확인)", r"(?:않|아니|필요)"),
            "권장 교양 과목": (r"권장", r"(?:필수|의무)", r"(?:아니|아닌|않)"),
            "졸업논문이 0학점인데 안 들으면 Fail인가요?": (r"0\s*학점", r"(?:Fail|FAIL|fail)", r"이수"),
            "심층상담 횟수 기준": (r"0\s*학점", r"1\s*(?:회|번)", r"(?:최소|이상)"),
        }
        for question, patterns in required.items():
            with self.subTest(question=question):
                result = self.answer(question)
                self.assertEqual("supported", result.status)
                for pattern in patterns:
                    self.assertRegex(result.conversational_answer, pattern)

    def test_in_process_api_schema_core_parity_and_legacy_fields_remain_separate(self):
        with patch("academic_assistant.api._chat_engine", return_value=self.facade), patch("academic_assistant.api._engine", return_value=self.engine), TestClient(app) as client:
            for question in ("졸업 총학점 기준", "전공필수 및 전공선택 기준", "졸업학점과 장학금 신청일"):
                payload = request(question).model_dump(mode="json")
                self.validators["academic-chat-request"].validate(payload)
                response = client.post("/v1/academic/chat", json=payload)
                self.assertEqual(200, response.status_code)
                data = response.json()
                self.validators["academic-chat-response"].validate(data)
                standalone = {key: value for key, value in payload.items()
                              if key not in {"previous_question", "generate_answer", "response_style"}}
                canonical = client.post("/v1/academic/answers", json=standalone)
                self.assertEqual(200, canonical.status_code)
                for key in ("status", "answer", "intent_ids", "calculations", "evidence_packet"):
                    self.assertEqual(canonical.json()[key], data[key], key)
            for extra in ({"response_style": "friendly"}, {"generate_answer": False}, {"previous_question": "졸업 총학점 기준"}):
                self.assertEqual(422, client.post("/v1/academic/answers", json={**standalone, **extra}).status_code)
        legacy = self.facade.chat(AcademicChatRequest(question="졸업 총학점 기준", admission_year=2026,
                    matched_curriculum_year=2026, department="컴퓨터공학과"))
        self.assertNotIn("response_style", request(response_style=None).model_dump())
        for key in ("conversational_answer", "presentation_claim_ids", "context_question"):
            self.assertNotIn(key, legacy.model_dump(mode="json", exclude_none=True))

    def test_api_rejects_private_forged_and_malformed_context_before_model(self):
        with patch("academic_assistant.api._chat_engine", return_value=self.facade), TestClient(app) as client:
            for extra in ({"history": []}, {"context_question": "졸업 총학점 기준"},
                          {"presentation_claim_ids": ["private"]}, {"response_style": "official"},
                          {"previous_question": " "}, {"previous_question": "가" * 501},
                          {"previous_question": {"question": "졸업"}}, {"generate_answer": "false"}):
                payload = request().model_dump(mode="json"); payload.update(extra)
                self.assertEqual(422, client.post("/v1/academic/chat", json=payload).status_code)
            for question, previous in (("제 이름은 홍길동이고 학번은 20260001입니다", None),
                                       ("그럼 몇 학점인가요?", "홍길동 학번 20260001 졸업학점")):
                payload = request(question, previous_question=previous).model_dump(mode="json")
                response = client.post("/v1/academic/chat", json=payload)
                self.assertEqual(422, response.status_code)
                self.assertNotIn("홍길동", response.text)
                self.assertNotIn("20260001", response.text)
        self.assertEqual([], self.no_model.calls)

    def test_optional_refinement_has_public_only_payload_and_shared60_second_budget(self):
        wire = PublicWire()
        provider = LocalLLMClient(LLMSettings("ollama", "http://127.0.0.1:11434", "synthetic-gemma", min_interval_seconds=60))
        provider._post_grounded = wire
        facade = GroundedChatEngine(self.engine, provider)
        captured = request("졸업에 필요한 전체 학점을 알려주실래요?", earned_credits={"credits.graduation.total": 107})
        fast = facade.chat(captured)
        self.assertEqual([], wire.calls)
        refined = facade.chat(captured.model_copy(update={"generate_answer": True}))
        self.assertEqual("generated", refined.generation_status)
        self.assertEqual(1, len(wire.calls))
        for key in ("packet_id", "answer", "status", "intent_ids", "calculations", "evidence_packet"):
            self.assertEqual(fast.model_dump()[key], refined.model_dump()[key], key)
        serialized = json.dumps(wire.calls[0], ensure_ascii=False)
        for private in (captured.question, "earned_credits", "student_facts", "previous_question", "transcript", "107", fast.answer):
            self.assertNotIn(private, serialized)
        self.assertEqual(512, wire.calls[0]["options"]["num_predict"])
        self.assertGreaterEqual(llm._budget.next_allowed - llm.time.monotonic(), 59)
        second = facade.chat(request("전공선택 학점 기준", generate_answer=True))
        self.assertEqual("busy", second.generation_status)
        self.assertEqual(1, len(wire.calls))

    def test_forged_friendly_presentation_and_unsupported_context_rejected(self):
        good = self.answer("전공필수 및 전공선택 기준").model_dump()
        for ids in ([], list(reversed(good["presentation_claim_ids"])),
                    [good["presentation_claim_ids"][0]], ["invented"]):
            with self.subTest(ids=ids), self.assertRaises(ValidationError):
                GroundedChatResponse.model_validate({**good, "presentation_claim_ids": ids})
        refused = self.answer("그럼 몇 학점 남았어?").model_dump()
        with self.assertRaises(ValidationError):
            GroundedChatResponse.model_validate({**refused, "context_question": "졸업학점 기준"})
        with self.assertRaises(ValidationError):
            GroundedChatResponse.model_validate({**good, "conversational_answer": ""})

    def test_transcript_followup_reuses_only_current_confirmed_rows(self):
        assessor = TranscriptAssessor(self.engine)
        initial = assessor.followup(TranscriptFollowupRequest(question="졸업까지 몇 학점 더 필요해?", transcript=transcript(), response_style="friendly"))
        self.assertEqual("supported", initial.status)
        self.assertEqual(127, initial.selected_checks[0].gap)
        self.assertTrue(initial.context_question)
        for courses, gap in (([row(credits=10)], 120), ([row(grade="F")], 130)):
            follow = assessor.followup(TranscriptFollowupRequest(question="그럼 몇 학점 남았어?", transcript=transcript(courses),
                              response_style="friendly", previous_question=initial.context_question))
            self.assertEqual("supported", follow.status)
            self.assertTrue(follow.context_used)
            self.assertEqual(gap, follow.selected_checks[0].gap)
            self.assertNotIn("calculations", follow.model_dump())
            self.assertNotIn("evidence_packet", follow.model_dump())
            self.assertRegex(follow.conversational_answer, r"(?:최종|공식).*졸업.*(?:아니|않|별도\s*확인|확정(?:하지|할\s*수\s*없))")

    def test_transcript_needs_review_and_nonofficial_claims_survive_friendly_style(self):
        assessor = TranscriptAssessor(self.engine)
        for updates in ({"record_complete": False}, {"degree_track": "unknown"}):
            result = assessor.followup(TranscriptFollowupRequest(question="졸업까지 몇 학점 더 필요해?",
                      transcript=transcript(**updates), response_style="friendly"))
            self.assertEqual("insufficient_evidence", result.status)
            self.assertIsNone(result.context_question)
            self.assertTrue(result.conversational_answer)
            self.assertTrue(result.selected_checks)
            for check in result.selected_checks:
                self.assertEqual("needs_review", check.result)
                self.assertEqual("insufficient_evidence", check.evidence_packet.status)
                self.assertEqual([], check.evidence_packet.applied_rules)
                self.assertEqual({}, check.policy_packet.student_facts)
        required_course = row(course_name="운영체제", course_code="CDA0017", category="major_required")
        result = assessor.followup(TranscriptFollowupRequest(question="남은 필수과목 알려줘", transcript=transcript([required_course]), response_style="friendly"))
        missing = result.selected_checks[0].missing_courses
        self.assertNotIn("운영체제", missing)
        self.assertEqual(8, len(missing))
        for label in missing:
            self.assertIn(label, result.conversational_answer)

    def test_incompatible_current_facts_never_become_supported_stripped_probe(self):
        cases = (("졸업 총학점 알려줘", None),
                 ("다시 설명해 주세요", "졸업 총학점 기준"))
        with patch("academic_assistant.api._chat_engine", return_value=self.facade), TestClient(app) as client:
            for question, previous in cases:
                with self.subTest(question=question, previous=previous):
                    payload = request(question, previous_question=previous,
                                      earned_credits={"credits.major.total": 60}).model_dump(mode="json")
                    response = client.post("/v1/academic/chat", json=payload)
                    self.assertEqual(422, response.status_code, "Do not silently drop an incompatible structured metric")
                    self.assertEqual([], self.no_model.calls)

    def test_comparator_reversal_cannot_become_a_supported_threshold_answer(self):
        for question in ("졸업 총학점은 최대 몇 학점인가요?", "교양 인정 상한은 최소 몇 학점인가요?"):
            with self.subTest(question=question):
                result = self.answer(question)
                self.assertIn(result.status, {"insufficient_evidence", "conflict", "out_of_scope"})
                self.assertEqual(result.status, result.evidence_packet.status)
                self.assertEqual([], result.evidence_packet.applied_rules)
                self.assertEqual([], result.presentation_claim_ids)
                self.assertIsNone(result.context_question)

    def test_transcript_api_schema_parity_refusal_and_legacy_omissions(self):
        with patch("academic_assistant.api._engine", return_value=self.engine), patch("academic_assistant.api._chat_engine", side_effect=AssertionError("Transcript must not invoke policy/model facade")), TestClient(app) as client:
            for question in ("졸업까지 몇 학점 더 필요해?", "남은 필수과목 알려줘", "PCCP로 논문 자동 면제?", "2025학번 교양 몇 학점 부족해?"):
                friendly = TranscriptFollowupRequest(question=question, transcript=transcript(), response_style="friendly")
                payload = friendly.model_dump(mode="json")
                self.validators["transcript-followup-request"].validate(payload)
                response = client.post("/v1/academic/transcripts/chat", json=payload)
                self.assertEqual(200, response.status_code)
                data = response.json()
                self.validators["transcript-followup-response"].validate(data)
                legacy_payload = {key: value for key, value in payload.items() if key not in {"response_style", "previous_question"}}
                legacy = client.post("/v1/academic/transcripts/chat", json=legacy_payload)
                self.assertEqual(200, legacy.status_code)
                for key in ("answer", "status", "selected_checks", "focus_check_ids", "verification_items"):
                    self.assertEqual(legacy.json()[key], data[key], key)
                for key in ("conversational_answer", "context_question", "context_used"):
                    self.assertNotIn(key, legacy.json())
                self.assertNotIn("generation_status", data)
                self.assertNotIn("evidence_packet", data)
            unsafe = TranscriptFollowupRequest(question="그럼 몇 학점 남았어?", transcript=transcript(), response_style="friendly", previous_question="홍길동 학번 20260001 졸업학점").model_dump(mode="json")
            response = client.post("/v1/academic/transcripts/chat", json=unsafe)
            self.assertEqual(422, response.status_code)
            self.assertNotIn("20260001", response.text)
            unsafe["previous_question"] = "졸업까지 몇 학점 더 필요해?"
            unsafe["transcript"]["confirmed"] = False
            self.assertEqual(422, client.post("/v1/academic/transcripts/chat", json=unsafe).status_code)


if __name__ == "__main__":
    unittest.main()
