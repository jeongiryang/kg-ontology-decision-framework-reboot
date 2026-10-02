"""Independent purpose-dialogue regressions designed before producer freeze.

Expected facts come from the pinned, source-verified catalogue and approved
RuleFacts, never from a question generator or product answer helper. These are
functional facade/API tests with an untrusted scripted semantic provider. They
do not establish live-model interpretation or actual Neo4j retrieval coverage.
During the design task only syntax is compiled; execution belongs to the later
frozen-implementation QA task. No held-out generator questions appear here.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.assistant_models import AssistantTurnRequest
from academic_assistant.core import AnswerEngine
from academic_assistant.registry import Registry


CATEGORY_LABELS = {"major_required": "전공필수", "major_elective": "전공선택"}
SCOPE = dict(admission_year=2026, matched_curriculum_year=2026, department="컴퓨터공학과")
REQUIRED_SET = "cwnu.cs.2026.major-required-course-set"
COUNSELING = "cwnu.cs.2026.major-counseling-completion"
THESIS_RESULT = "cwnu.cs.2026.graduation.thesis-completion-result"
WORK_PREREQUISITE = "cwnu.cs.2026.operations.graduation-work-prerequisite"
OVERVIEW_RULES = {
    "cwnu.cs.2026.credits.graduation-total",
    "cwnu.cs.2026.credits.general-total",
    "cwnu.cs.2026.credits.major-total",
    "cwnu.cs.2026.credits.graduation-remaining",
    "cwnu.cs.2026.credits.graduation-remaining-allocation",
    "cwnu.cs.2026.credits.general-foundation",
    "cwnu.cs.2026.credits.general-balanced",
    "cwnu.cs.2026.credits.general-remaining",
    "cwnu.cs.2026.credits.general-remaining-allocation",
    "cwnu.cs.2026.credits.general-recognition-cap",
    "cwnu.cs.2026.general-balanced-area-coverage",
    "cwnu.cs.2026.credits.major-required",
    "cwnu.cs.2026.credits.major-elective",
    "cwnu.cs.2026.credits.major-minimum",
    "cwnu.cs.2026.credits.major-advanced",
    REQUIRED_SET,
    "cwnu.cs.2026.graduation.thesis-required",
    THESIS_RESULT,
    COUNSELING,
}


def canonical_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def turn(question, **updates):
    payload = dict(question=question, earned_credits={}, **SCOPE)
    payload.update(updates)
    return AssistantTurnRequest.model_validate(payload)


def planned(*requests, context_used=False):
    return {"requests": list(requests), "context_used": context_used}


def course_request(name=None, *, category=None, properties=None, purpose="attributes", legacy=False):
    filters = {}
    if name is not None:
        filters["name"] = name
    if category is not None:
        filters["category"] = category
    request = {"kind": "courses", "filters": filters}
    if not legacy:
        request["purpose"] = purpose
        if properties is not None:
            request["properties"] = list(properties)
    return request


def rule_request(*intent_ids):
    return {"kind": "rule", "intent_ids": list(intent_ids)}


def source_writer(*, properties=("credits",), replace_text=None, suffix=""):
    """Produce untrusted sections without using a product permitted-text oracle.

    Wording is input to the writer validator, not the expected answer. Tests
    independently assert source identity, values, property projection and holds.
    """
    def write(payload):
        sections = []
        for part in payload["parts"]:
            facts = part["facts"]
            course_facts = [fact for fact in facts if "course_name" in fact]
            # New writer payloads intentionally expose only requested columns.
            # Keep legacy omission tests controlled by the fixture's properties.
            active_properties = set(properties) & set(part.get("properties", properties))
            sentences = []
            if course_facts and "count" in active_properties:
                categories = {fact["category"] for fact in course_facts if "category" in fact}
                summary_category = part.get("course_summary", {}).get("filters", {}).get("category")
                if summary_category is not None:
                    categories.add(summary_category)
                label = CATEGORY_LABELS[next(iter(categories))] if len(categories) == 1 else "조회한"
                sentences.append(f"{label} 과목은 {len(course_facts)}개입니다.")
            for fact in facts:
                if "course_name" in fact:
                    if "count" in active_properties:
                        continue
                    name = fact["course_name"]
                    fragments = []
                    if "credits" in active_properties:
                        fragments.append(f"{fact['credits']}학점")
                    if "category" in active_properties:
                        fragments.append(CATEGORY_LABELS[fact["category"]])
                    if "offering" in active_properties:
                        fragments.append(fact["offering_label"])
                    if "code" in active_properties:
                        fragments.append(fact["code"])
                    sentences.append(name + (": " + ", ".join(fragments) if fragments else "") + ".")
                else:
                    sentences.append(fact["statement"])
                    items = fact.get("outcome", {}).get("items", [])
                    if items:
                        sentences.append("해당 항목: " + ", ".join(item["label"] for item in items) + ".")
            text = " ".join(sentences)
            if replace_text is not None:
                text = replace_text(part, text)
            sections.append({"part_id": part["part_id"], "text": text + suffix,
                             "fact_ids": [fact["fact_id"] for fact in facts]})
        return {"sections": sections}
    return write


class ScriptedSemantic:
    """Mock only provider documents; real routing, retrieval and output remain."""
    def __init__(self, document, writer, *, typed_plan=False):
        self.document, self.writer = deepcopy(document), writer
        self.typed_plan = typed_plan
        self.typed_plans = typed_plan
        if typed_plan and isinstance(self.document, dict) and "requests" in self.document:
            self.document["coverage"] = deepcopy(self.document["requests"])
        self.plan_calls, self.write_calls = [], []
        self.entered = self.exited = 0

    @contextmanager
    def session(self):
        self.entered += 1
        try:
            yield self
        finally:
            self.exited += 1

    def plan(self, payload):
        from academic_assistant.semantic_llm import SemanticDocument
        self.plan_calls.append(deepcopy(payload))
        if isinstance(self.document, Exception):
            raise self.document
        return SemanticDocument(document=deepcopy(self.document), cached=False, typed_plan=self.typed_plan)

    def write(self, payload):
        from academic_assistant.semantic_llm import SemanticDocument
        self.write_calls.append(deepcopy(payload))
        if isinstance(self.writer, Exception):
            raise self.writer
        return SemanticDocument(document=self.writer(deepcopy(payload)), cached=False)


class PurposeDialogueIndependentQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = json.loads((ROOT / "knowledge/course-catalogue.json").read_text(encoding="utf-8"))
        cls.raw_courses = {fact["course_code"]: fact for fact in cls.catalogue["courses"]}
        cls.raw_rules = {}
        for path in sorted((ROOT / "knowledge/rules").glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            cls.raw_rules[value["rule_id"]] = value
        cls.raw_sources = {}
        for path in sorted((ROOT / "knowledge/sources").glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            cls.raw_sources[value["source_id"]] = value
        cls.registry = Registry.load(ROOT)

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ACADEMIC_LLM_PROVIDER": "disabled"}).start()
        self.engine = AnswerEngine(self.registry)

    def chat(self, question, document, *, writer=None, typed_plan=False, **updates):
        from academic_assistant.assistant import SemanticAssistant
        provider = ScriptedSemantic(document, writer or source_writer(), typed_plan=typed_plan)
        response = SemanticAssistant(engine=self.engine, llm=provider).chat(turn(question, **updates))
        self.assertEqual(provider.entered, provider.exited)
        self.assertLessEqual(len(provider.plan_calls), 1)
        self.assertLessEqual(len(provider.write_calls), 2)
        self.assertIn(response.status, {"supported", "insufficient_evidence", "conflict", "out_of_scope"})
        self.assertLessEqual(len(response.parts), 8)
        self.assert_no_private_output(response)
        return response, provider

    def assert_no_private_output(self, response):
        self.assertNotRegex(response.model_dump_json(), r"(?i)(?:[A-Z]:[\\/]|QAPRIVATE0001)")

    def course_parts(self, response):
        return [part for part in response.parts if part.course_evidence is not None and part.status == "supported"]

    def assert_courses(self, response, expected_codes):
        parts = self.course_parts(response)
        actual = [fact for part in parts for fact in part.course_evidence.courses]
        self.assertCountEqual(expected_codes, [fact.course_code for fact in actual])
        for part in parts:
            self.assertIsNone(part.evidence_packet, "Obligation and catalogue facts require separate public parts")
            packet = part.course_evidence
            self.assertEqual("supported", packet.status)
            self.assertEqual(SCOPE, packet.scope.model_dump())
            self.assertEqual(len(packet.courses), len(packet.evidence))
            for fact, citation in zip(packet.courses, packet.evidence, strict=True):
                expected = self.raw_courses[fact.course_code]
                self.assertEqual(expected, fact.model_dump())
                self.assertEqual(expected["fact_sha256"], canonical_hash({k: v for k, v in expected.items() if k != "fact_sha256"}))
                self.assertEqual((expected["course_id"], expected["source_id"], expected["source_sha256"], expected["locator"]),
                                 (citation.course_id, citation.source_id, citation.source_sha256, citation.locator))
                self.assertEqual("approved", self.registry.sources[citation.source_id]["review"]["status"])
        return parts

    def assert_rules(self, response, required_rule_ids=()):
        actual_ids = set()
        for part in response.parts:
            if part.status != "supported" or part.evidence_packet is None:
                continue
            self.assertIsNone(part.course_evidence)
            packet = part.evidence_packet
            self.assertEqual("supported", packet.status)
            self.assertEqual(SCOPE, packet.scope.model_dump())
            for applied in packet.applied_rules:
                actual_ids.add(applied.rule_id)
                expected = self.raw_rules[applied.rule_id]
                self.assertEqual(expected, self.registry.rules[applied.rule_id])
                self.assertEqual(("human", "full", "approved"),
                                 tuple(expected["review"][key] for key in ("mode", "scope", "status")))
                self.assertEqual(canonical_hash(expected), applied.rule_sha256)
                self.assertEqual([2026], expected["applicability"]["admission_years"])
                self.assertEqual([2026], expected["applicability"]["curriculum_years"])
                self.assertEqual(["컴퓨터공학과"], expected["applicability"]["departments"])
                citations = [cite for cite in packet.evidence if cite.rule_id == applied.rule_id]
                self.assertTrue(citations)
                locations = {(ref["source_id"], ref["locator"]) for ref in expected["evidence"]}
                for cite in citations:
                    self.assertIn((cite.source_id, cite.locator), locations)
                    self.assertEqual(expected["decision"]["statement"], cite.claim)
                    # Rule EvidenceReference has no source_sha256 wire field.
                    # Pin its source identity/hash/approval through raw SourceEntry.
                    source = self.raw_sources[cite.source_id]
                    self.assertEqual(source, self.registry.sources[cite.source_id])
                    self.assertEqual(source["source_id"], cite.source_id)
                    self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
                    self.assertEqual(("human", "full", "approved"),
                                     tuple(source["review"][key] for key in ("mode", "scope", "status")))
        self.assertTrue(set(required_rule_ids) <= actual_ids, f"Missing required rule evidence: {set(required_rule_ids) - actual_ids}")
        return actual_ids

    def assert_projection(self, response, code, properties):
        expected = self.raw_courses[code]
        body = response.answer + " " + " ".join(part.text for part in response.parts)
        self.assertIn(expected["course_name"], body)
        if "credits" in properties:
            self.assertRegex(body, rf"{expected['credits']}\s*학점")
        else:
            self.assertNotRegex(body, r"\d+\s*학점")
        if "category" in properties:
            patterns = {"major_required": r"(?:전공\s*필수|전필)", "major_elective": r"(?:전공\s*선택|전선)"}
            self.assertRegex(body, patterns[expected["category"]])
        else:
            self.assertNotRegex(body, r"전공\s*(?:필수|선택)|전필|전선")
        if "offering" in properties:
            self.assertIn(expected["offering_label"], body)
        else:
            self.assertNotRegex(body, r"(?<!\d)[1-4]\s*학년|(?<!\d)[1-2]\s*학기|하계|동계")
        if "code" in properties:
            self.assertIn(code, body)
        else:
            self.assertNotIn(code, body)

    def test_requested_credit_category_offering_and_code_are_projected_in_body(self):
        cases = (
            ("컴퓨터구조는 몇 학점이야?", "CDA0016", "컴퓨터구조", ("credits",)),
            ("컴퓨터구조는 전필이야 전선이야?", "CDA0016", "컴퓨터구조", ("category",)),
            ("고급자료구조 편성 학년과 학기를 알려줘", "CDA0143", "고급자료구조", ("offering",)),
            ("컴퓨터구조 과목 코드만 알려줘", "CDA0016", "컴퓨터구조", ("code",)),
            ("표준현장실습4의 학점은?", "CDA0173", "표준현장실습4", ("credits",)),
            ("심층상담은 몇 학점이야?", "CDA0088", "심층상담", ("credits",)),
        )
        for question, code, name, properties in cases:
            with self.subTest(question=question):
                response, _ = self.chat(question, planned(course_request(name, properties=properties)),
                                        writer=source_writer(properties=properties))
                self.assertEqual("supported", response.status)
                self.assertEqual("generated", response.generation_status, "A correct concise writer must be usable")
                self.assert_courses(response, [code])
                self.assert_projection(response, code, properties)

    def test_multiple_offering_years_and_terms_use_exact_approved_offering(self):
        for name, code in (("객체지향프로그래밍", "CDA0155"), ("표준현장실습3", "CDA0172")):
            with self.subTest(name=name):
                response, _ = self.chat(name + " 편성 학년과 학기만 알려줘", planned(course_request(name, properties=("offering",))),
                                        writer=source_writer(properties=("offering",)))
                self.assertEqual("supported", response.status)
                self.assert_courses(response, [code])
                self.assert_projection(response, code, ("offering",))
                self.assertNotIn("미확인", response.answer)

    def test_false_elective_premise_is_corrected_without_filtering_out_named_course(self):
        question = "컴퓨터구조는 전선 과목이야?"
        correction = source_writer(properties=("category",), replace_text=lambda _part, _text:
                                   "컴퓨터구조는 전공선택이 아니라 전공필수입니다.")
        for category in (None, "major_elective"):
            with self.subTest(untrusted_category_filter=category):
                response, _ = self.chat(question, planned(course_request("컴퓨터구조", category=category, properties=("category",))), writer=correction)
                self.assertEqual("supported", response.status)
                self.assertEqual("generated", response.generation_status)
                self.assert_courses(response, ["CDA0016"])
                self.assert_projection(response, "CDA0016", ("category",))
                self.assertRegex(response.answer, r"(?:전공선택|전선).{0,12}(?:아니|아닌)")

    def test_completion_obligation_is_supported_by_required_set_in_a_separate_rule_part(self):
        for name, code, extra in (("컴퓨터구조", "CDA0016", ()), ("심층상담", "CDA0088", (COUNSELING,)),
                                  ("졸업논문", "CDA0034", (THESIS_RESULT,))):
            with self.subTest(name=name):
                response, _ = self.chat(name + "는 졸업하려면 반드시 이수해야 해?",
                                        planned(course_request(name, purpose="completion_obligation")),
                                        writer=source_writer(properties=("category",)))
                self.assertEqual("supported", response.status)
                self.assert_rules(response, (REQUIRED_SET, *extra))
                # Main decision 00f8a8d: the approved required-set code/label
                # itself proves obligation; no duplicate catalogue part required.
                required_items = {item["item_id"]: item["label"] for item in
                                  self.raw_rules[REQUIRED_SET]["decision"]["outcome"]["items"]}
                self.assertEqual(self.raw_courses[code]["course_name"], required_items[code])
                self.assertIn(required_items[code], response.answer)
                if self.course_parts(response):
                    self.assert_courses(response, [code])
                self.assertRegex(response.answer, r"이수.{0,14}(?:해야|필요|필수|의무)|반드시.{0,14}이수")
                self.assertNotRegex(response.answer, r"이수할\s*필요(?:가)?\s*없|이수하지\s*않아도")
                if COUNSELING in extra:
                    self.assertRegex(response.answer, r"0\s*학점")
                    self.assertRegex(response.answer, r"최소\s*1\s*(?:회|번)|1\s*(?:회|번)\s*이상")
                if THESIS_RESULT in extra:
                    self.assertRegex(response.answer, r"0\s*학점")
                    self.assertRegex(response.answer, r"(?i)FAIL")

    def test_required_completion_does_not_approve_attendance_or_personal_substitution_exemption(self):
        questions = (
            "컴퓨터구조는 전필이니까 출석을 안 해도 이수 인정돼?",
            "제가 복학생인데 컴퓨터구조 출석 면제를 자동으로 받을 수 있어?",
            "자료구조를 들었으니 고급자료구조는 대체 인정으로 면제받아?",
            "학석사연계과정인 저는 졸업논문이 자동 면제돼?",
        )
        for question in questions:
            name = "졸업논문" if "졸업논문" in question else ("고급자료구조" if "고급자료구조" in question else "컴퓨터구조")
            with self.subTest(question=question):
                response, _ = self.chat(question, planned(course_request(name, purpose="completion_obligation")),
                                        writer=source_writer(properties=("category",)))
                self.assertNotEqual("supported", response.status)
                self.assertTrue(response.kind in {"clarification", "refusal"} or any(p.status != "supported" for p in response.parts))
                self.assert_rules(response)
                self.assertNotRegex(response.answer, r"자동(?:으로)?\s*면제(?:됩니다|돼요|된다)|출석하지\s*않아도\s*(?:이수|졸업)\s*가능")

    def test_elective_classification_cannot_prove_individual_graduation_exemption(self):
        response, _ = self.chat("네트워크프로그래밍은 전선이니 안 들어도 제가 졸업할 수 있지?",
                                planned(course_request("네트워크프로그래밍", purpose="completion_obligation")),
                                writer=source_writer(properties=("category",)))
        self.assertNotEqual("supported", response.status)
        self.assertNotRegex(response.answer, r"안\s*들어도\s*졸업(?:할\s*수|이)?\s*가능|자동(?:으로)?\s*면제(?:됩니다|돼요|된다)")
        self.assert_rules(response)

    def test_elective_capstone_has_conditional_graduation_work_prerequisite_evidence(self):
        response, _ = self.chat("졸업작품을 수강하려면 산학캡스톤디자인2를 PASS해야 해?",
                                planned(course_request("산학캡스톤디자인2", purpose="completion_obligation")),
                                writer=source_writer(properties=("category",)))
        self.assertEqual("supported", response.status)
        self.assert_rules(response, (WORK_PREREQUISITE,))
        if self.course_parts(response):
            self.assert_courses(response, ["CDA0168"])
        self.assertRegex(response.answer, r"(?:캡스톤디자인\s*(?:II|Ⅱ|2)).{0,20}(?:PASS|Pass|통과)")
        self.assertNotRegex(response.answer, r"졸업(?:이)?\s*확정|학점(?:을)?\s*자동(?:으로)?\s*취득")

    def test_bare_course_or_alias_without_clear_property_asks_for_property(self):
        for question in ("컴퓨터구조", "컴구", "고급자료구조"):
            with self.subTest(question=question):
                response, provider = self.chat(question, planned(course_request(question, properties=("credits",))))
                self.assertEqual("clarification", response.kind)
                self.assertNotEqual("supported", response.status)
                self.assertLessEqual(len(response.answer), 500)
                self.assertRegex(response.answer, r"학점|이수구분|전필|전선|학기|어떤|무엇")
                self.assertFalse(any(part.status == "supported" for part in response.parts))
                self.assertEqual([], provider.write_calls)

    def test_bare_new_course_inherits_previous_credit_property_and_changes_subject(self):
        first, _ = self.chat("컴퓨터구조는 몇 학점이야?", planned(course_request("컴퓨터구조", properties=("credits",))))
        self.assertEqual("supported", first.status)
        self.assertTrue(first.context_question)
        second, _ = self.chat("고급자료구조", planned(course_request("고급자료구조", properties=("credits",)), context_used=True),
                               previous_question=first.context_question)
        self.assertEqual("supported", second.status)
        self.assertTrue(second.context_used)
        self.assert_courses(second, ["CDA0143"])
        self.assert_projection(second, "CDA0143", ("credits",))
        self.assertNotIn("컴퓨터구조", second.answer)

    def test_ambiguous_previous_properties_do_not_pick_one_for_a_bare_new_course(self):
        previous = "컴퓨터구조의 학점과 이수구분을 함께 알려줘"
        response, provider = self.chat("고급자료구조", planned(course_request("고급자료구조", properties=("credits",)), context_used=True),
                                       previous_question=previous)
        # Main decision 00f8a8d: a clear plural-property context may carry both.
        if response.status == "supported":
            self.assert_courses(response, ["CDA0143"])
            self.assert_projection(response, "CDA0143", ("credits", "category"))
        else:
            self.assertNotEqual("no_matching_evidence", response.reason_code)
            self.assertTrue(response.kind == "clarification" or any(part.status != "supported" for part in response.parts))

    def test_clear_aliases_retrieve_exact_source_course_and_requested_property(self):
        from academic_assistant.courses import retrieve_courses
        for alias, code in (("컴구", "CDA0016"), ("고자구", "CDA0143"), ("네프", "CDA0165")):
            with self.subTest(alias=alias):
                packet = retrieve_courses(self.engine, {"name": alias})
                self.assertEqual("supported", packet.status)
                self.assertEqual([code], [fact.course_code for fact in packet.courses])
                self.assertEqual(self.raw_courses[code], packet.courses[0].model_dump())
                response, _ = self.chat(alias + "는 전필이야 전선이야?", planned(course_request(alias, properties=("category",))),
                                        writer=source_writer(properties=("category",)))
                self.assertEqual("supported", response.status)
                self.assert_courses(response, [code])
                self.assert_projection(response, code, ("category",))

    def mixed_clarification(self):
        response, provider = self.chat("고급컴퓨자료터구조는 전필 과목이야?",
                                      planned(course_request("고급컴퓨자료터구조", properties=("category",))),
                                      writer=source_writer(properties=("category",)))
        self.assertEqual("clarification", response.kind)
        self.assertEqual("insufficient_evidence", response.status)
        self.assertEqual("ambiguous", response.reason_code)
        self.assertEqual([], provider.write_calls)
        self.assertFalse(any(part.status == "supported" for part in response.parts))
        self.assertLessEqual(len(response.answer), 500)
        for name in ("고급자료구조", "컴퓨터구조"):
            self.assertIn(name, response.answer)
        self.assertTrue(response.context_question)
        self.assertLessEqual(len(response.context_question), 500)
        for name in ("고급자료구조", "컴퓨터구조"):
            self.assertIn(name, response.context_question)
        return response

    def test_mixed_name_requests_real_candidates_instead_of_first_fuzzy_course(self):
        self.mixed_clarification()

    def test_candidate_confirmation_keeps_category_property_and_selected_identity(self):
        initial = self.mixed_clarification()
        for name, code in (("고급자료구조", "CDA0143"), ("컴퓨터구조", "CDA0016")):
            with self.subTest(selection=name):
                response, _ = self.chat(name + "를 말한 거야", planned(course_request(name, properties=("category",)), context_used=True),
                                        writer=source_writer(properties=("category",)), previous_question=initial.context_question)
                self.assertEqual("supported", response.status)
                self.assertTrue(response.context_used)
                self.assert_courses(response, [code])
                self.assert_projection(response, code, ("category",))

    def test_ordinal_candidate_choice_follows_the_presented_order(self):
        initial = self.mixed_clarification()
        ordered = sorted(("고급자료구조", "컴퓨터구조"), key=initial.answer.index)
        selected = ordered[1]
        code = next(code for code, fact in self.raw_courses.items() if fact["course_name"] == selected)
        response, _ = self.chat("두 번째 과목", planned(course_request(selected, properties=("category",)), context_used=True),
                                writer=source_writer(properties=("category",)), previous_question=initial.context_question)
        self.assertEqual("supported", response.status)
        self.assert_courses(response, [code])
        self.assert_projection(response, code, ("category",))

    def test_compound_required_and_elective_counts_cover_both_exact_source_sets(self):
        response, _ = self.chat("전필 과목 전선 과목 개수 몇 개야?",
                                planned(course_request(category="major_required", properties=("count",)),
                                        course_request(category="major_elective", properties=("count",))),
                                writer=source_writer(properties=("count",)))
        self.assertEqual("supported", response.status)
        self.assertEqual(43, len(self.raw_courses))
        parts = self.assert_courses(response, list(self.raw_courses))
        by_category = {}
        for part in parts:
            facts = part.course_evidence.courses
            categories = {fact.category for fact in facts}
            self.assertEqual(1, len(categories))
            category = next(iter(categories))
            by_category[category] = {fact.course_code for fact in facts}
            expected_count = {"major_required": 9, "major_elective": 34}[category]
            self.assertRegex(part.text, rf"{expected_count}\s*(?:개|과목)")
            self.assertNotRegex(part.text, r"\d+\s*학점|CDA\d+|\d+\s*학년")
            self.assertFalse(any(fact.course_name in part.text for fact in facts))
        for category, count in (("major_required", 9), ("major_elective", 34)):
            expected = {code for code, fact in self.raw_courses.items() if fact["category"] == category}
            self.assertEqual(count, len(expected))
            self.assertEqual(expected, by_category[category])

    def test_dropped_compound_count_cannot_be_supported_or_mislabeled_evidence_gap(self):
        response, _ = self.chat("전필 과목 전선 과목 개수 몇 개야?",
                                planned(course_request(category="major_required", properties=("count",))),
                                writer=source_writer(properties=("count",)))
        # Main decision 00f8a8d: server-side recovery is valid only if every
        # final demand has the exact raw source set, count and evidence.
        if response.status == "supported":
            parts = self.assert_courses(response, list(self.raw_courses))
            actual = {}
            for part in parts:
                categories = {fact.category for fact in part.course_evidence.courses}
                self.assertEqual(1, len(categories))
                category = next(iter(categories))
                expected = {code for code, fact in self.raw_courses.items() if fact["category"] == category}
                actual[category] = {fact.course_code for fact in part.course_evidence.courses}
                self.assertEqual(expected, actual[category])
                self.assertRegex(part.text, re.escape(CATEGORY_LABELS[category]) + rf"[^.!?\n]{{0,40}}{len(expected)}\s*(?:개|과목)")
                self.assertFalse(any(fact.course_name in part.text for fact in part.course_evidence.courses))
            self.assertEqual(set(CATEGORY_LABELS), set(actual))
        else:
            self.assertNotEqual("no_matching_evidence", response.reason_code)
            self.assertTrue(response.kind in {"clarification", "refusal"} or any(p.status != "supported" for p in response.parts))

    def test_two_named_credit_questions_do_not_omit_or_reuse_the_previous_subject(self):
        question = "컴퓨터구조와 표준현장실습4는 각각 몇 학점이야?"
        complete, _ = self.chat(question, planned(course_request("컴퓨터구조", properties=("credits",)),
                                                 course_request("표준현장실습4", properties=("credits",))))
        self.assertEqual("supported", complete.status)
        self.assert_courses(complete, ["CDA0016", "CDA0173"])
        for name, credits in (("컴퓨터구조", 3), ("표준현장실습4", 12)):
            self.assertRegex(complete.answer, re.escape(name) + rf"[^.!?\n]{{0,50}}{credits}\s*학점")
        incomplete, _ = self.chat(question, planned(course_request("컴퓨터구조", properties=("credits",))))
        self.assertNotEqual("supported", incomplete.status)
        self.assertNotEqual("no_matching_evidence", incomplete.reason_code)

    def test_graduation_overview_expands_approved_rules_within_eight_parts(self):
        question = "졸업요건을 교양·전공 세부 학점과 배분, 인정 상한, 균형교양 영역, 전필 지정과목, 졸업논문·상담까지 정리해줘"
        response, _ = self.chat(question, planned({"kind": "requirements_overview"}), writer=source_writer())
        self.assertEqual("supported", response.status)
        self.assertLessEqual(len(response.parts), 8)
        self.assertEqual([], self.course_parts(response), "Overview authority must be academic RuleFacts")
        self.assert_rules(response, OVERVIEW_RULES)
        for label, credits in (("총", 130), ("교양", 34), ("전공", 78), ("잔여", 18)):
            self.assertRegex(response.answer, re.escape(label) + rf"[^.!?\n]{{0,45}}{credits}\s*학점")
        self.assertNotRegex(response.answer, r"43\s*(?:개|과목)")
        self.assertLessEqual(sum(fact["course_name"] in response.answer for fact in self.raw_courses.values()), 11)

    def test_graduation_overview_cannot_be_replaced_by_a_complete_course_catalogue(self):
        response, _ = self.chat("졸업요건 전체를 요약해서 알려줘", planned(course_request(properties=("names",))),
                                writer=source_writer(properties=("names",)))
        self.assertNotEqual("supported", response.status)
        self.assertNotEqual("no_matching_evidence", response.reason_code)

    def test_known_course_description_is_a_syllabus_evidence_gap_not_scope_or_outage(self):
        for name in ("컴퓨터구조", "고자구", "네프"):
            with self.subTest(name=name):
                response, _ = self.chat(name + " 수업에서 어떤 내용을 배우는지 알려줘",
                                        planned(course_request(name, purpose="description")),
                                        writer=source_writer(replace_text=lambda _part, _text: "CPU 설계와 실습 프로젝트를 배웁니다."))
                self.assertEqual("insufficient_evidence", response.status)
                self.assertEqual("no_matching_evidence", response.reason_code)
                self.assertNotIn("CPU 설계와 실습 프로젝트를 배웁니다.", response.answer)
                self.assertNotIn("processing_unavailable", str(response.reason_code))
                self.assert_rules(response)

    def test_projected_writer_still_rejects_wrong_values_extra_subjects_and_negated_obligation(self):
        variants = (
            ("컴퓨터구조는 몇 학점이야?", course_request("컴퓨터구조", properties=("credits",)), "컴퓨터구조는 4학점입니다."),
            ("컴퓨터구조는 몇 학점이야?", course_request("컴퓨터구조", properties=("credits",)), "컴퓨터구조는 3학점이고 양자역학도 3학점입니다."),
            ("컴퓨터구조는 전필이야 전선이야?", course_request("컴퓨터구조", properties=("category",)), "컴퓨터구조는 전공선택입니다."),
            ("고급자료구조 편성 학기는?", course_request("고급자료구조", properties=("offering",)), "고급자료구조는 3학년 1학기입니다."),
            ("컴퓨터구조는 반드시 이수해야 해?", course_request("컴퓨터구조", purpose="completion_obligation"), "컴퓨터구조는 이수할 필요가 없습니다."),
        )
        for question, request, false_text in variants:
            with self.subTest(false_text=false_text):
                response, _ = self.chat(question, planned(request), writer=source_writer(replace_text=lambda _part, _text: false_text))
                self.assertEqual("insufficient_evidence", response.status)
                self.assertEqual("fallback", response.generation_status)
                self.assertNotIn(false_text, response.answer)
                self.assertNotIn("양자역학", response.answer)
                if request["purpose"] == "completion_obligation":
                    self.assert_rules(response, (REQUIRED_SET,))
                else:
                    self.assert_courses(response, ["CDA0143" if "고급자료구조" in question else "CDA0016"])

    def test_universal_three_credit_claim_is_rejected_for_real_elective_catalogue(self):
        response, _ = self.chat("전선 과목들의 학점을 모두 알려줘",
                                planned(course_request(category="major_elective", properties=("names", "credits"))),
                                writer=source_writer(replace_text=lambda _part, _text: "모든 전공선택 과목은 3학점입니다."))
        self.assertEqual("insufficient_evidence", response.status)
        self.assertEqual("fallback", response.generation_status)
        self.assertNotIn("모든 전공선택 과목은 3학점입니다.", response.answer)
        expected = [code for code, fact in self.raw_courses.items() if fact["category"] == "major_elective"]
        self.assert_courses(response, expected)
        self.assertEqual({0, 3, 6, 12}, {self.raw_courses[code]["credits"] for code in expected})

    def test_required_coverage_labels_cannot_hide_a_negated_completion_predicate(self):
        outcome = self.raw_rules[REQUIRED_SET]["decision"]["outcome"]
        self.assertEqual(9, outcome["minimum_items"])
        self.assertEqual(1, outcome["minimum_courses_per_item"])
        labels = ", ".join(item["label"] for item in outcome["items"])
        forged = (self.raw_rules[REQUIRED_SET]["decision"]["statement"] + " 해당 항목: " + labels +
                  ". 지정 과목을 각각 이수해야 하며 한 과목으로 중복 충족할 수 없습니다." +
                  " 지정된 9개 과목을 이수할 필요가 없습니다.")
        response, _ = self.chat("전공필수 지정과목의 이수 의무를 알려줘",
                                planned(rule_request("major.required-course-set")),
                                writer=source_writer(replace_text=lambda _part, _text: forged))
        self.assertNotIn(forged, response.answer)
        self.assertNotRegex(response.answer, r"이수할\s*필요(?:가)?\s*없")
        self.assertEqual("fallback", response.generation_status)
        self.assert_rules(response, (REQUIRED_SET,))

    def test_balanced_area_coverage_rejects_reversed_cardinality_and_fixed_three_credits(self):
        rule_id = "cwnu.cs.2026.general-balanced-area-coverage"
        fact = self.raw_rules[rule_id]
        outcome = fact["decision"]["outcome"]
        self.assertEqual(4, outcome["minimum_items"])
        self.assertEqual(1, outcome["minimum_courses_per_item"])
        self.assertFalse(outcome["one_course_may_cover_multiple_items"])
        self.assertIsNone(outcome["fixed_credits_per_course"])
        labels = ", ".join(item["label"] for item in outcome["items"])
        distinct = " 한 과목으로 둘 이상의 영역을 동시에 충족할 수 없습니다."
        variants = (
            "균형교양은 4개 영역인 " + labels + "에서 각각 1과목 미만이어도 충족할 수 있습니다." +
            distinct + " 영역별 과목은 반드시 3학점일 필요가 없습니다.",
            "균형교양은 4개 영역인 " + labels + "에서 각각 1과목 이상 이수해야 합니다." +
            distinct + " 모든 영역별 과목은 3학점이어야 합니다.",
        )
        for forged in variants:
            with self.subTest(predicate="minimum_cardinality" if "미만" in forged else "unsupported_fixed_credits"):
                response, _ = self.chat("균형교양 영역별 이수 조건을 알려줘",
                                        planned(rule_request("general.balanced-area-coverage")),
                                        writer=source_writer(replace_text=lambda _part, _text: forged))
                self.assertEqual("insufficient_evidence", response.status)
                self.assertEqual("fallback", response.generation_status)
                self.assertNotIn(forged, response.answer)
                self.assert_rules(response, (rule_id,))

    def test_count_prose_can_name_only_its_exact_year_semester_filter_scope(self):
        filters = {"category": "major_required", "year": 3, "semester": 1}
        expected = [code for code, fact in self.raw_courses.items()
                    if fact["category"] == filters["category"] and filters["year"] in fact["offering_years"]
                    and str(filters["semester"]) in fact["offering_semesters"]]
        self.assertTrue(expected)
        def scoped_count(part, _text):
            summary = part["course_summary"]
            self.assertEqual(filters, summary["filters"])
            return f"3학년 1학기 편성 전공필수 과목은 {summary['course_count']}개입니다."
        request = {"kind": "courses", "filters": filters, "purpose": "attributes", "properties": ["count"]}
        response, _ = self.chat("3학년 1학기에 편성된 전공필수 과목은 몇 개야?", planned(request),
                                writer=source_writer(properties=("count",), replace_text=scoped_count))
        self.assertEqual("supported", response.status)
        self.assertEqual("generated", response.generation_status)
        self.assert_courses(response, expected)
        self.assertRegex(response.answer, r"3\s*학년\s*1\s*학기")
        self.assertRegex(response.answer, rf"{len(expected)}\s*(?:개|과목)")
        self.assertFalse(any(self.raw_courses[code]["course_name"] in response.answer for code in expected))
        self.assertNotRegex(response.answer, r"\d+\s*학점|CDA\d+")

    def test_rule_identity_cannot_replace_requested_course_credit_and_category_facts(self):
        expected = self.raw_courses["CDA0016"]
        self.assertEqual((3, "major_required"), (expected["credits"], expected["category"]))
        response, _ = self.chat("컴퓨터구조의 학점과 이수구분을 함께 알려줘",
                                planned(rule_request("major.required-course-set")), writer=source_writer())
        if response.status == "supported":
            self.assert_courses(response, ["CDA0016"])
            self.assert_projection(response, "CDA0016", ("credits", "category"))
        else:
            self.assertNotEqual("no_matching_evidence", response.reason_code)
            self.assertTrue(response.kind in {"clarification", "refusal"} or any(part.status != "supported" for part in response.parts))

    def test_r5_f1_area_source_minimum_cannot_be_replaced_by_three_courses(self):
        rule_id = "cwnu.cs.2026.general-balanced-area-coverage"
        outcome = self.raw_rules[rule_id]["decision"]["outcome"]
        self.assertEqual("area", outcome["coverage_kind"])
        self.assertEqual((4, 1, False, None),
                         (outcome["minimum_items"], outcome["minimum_courses_per_item"],
                          outcome["one_course_may_cover_multiple_items"], outcome["fixed_credits_per_course"]))
        labels = ", ".join(item["label"] for item in outcome["items"])
        forged = ("균형교양은 4개 영역인 " + labels + "에서 각각 3과목 이상 이수해야 합니다. "
                  "한 과목으로 둘 이상의 영역을 동시에 충족할 수 없습니다. "
                  "영역별 과목은 반드시 3학점일 필요가 없습니다.")
        response, _ = self.chat("균형교양 영역별 이수 조건을 알려줘",
                                planned(rule_request("general.balanced-area-coverage")),
                                writer=source_writer(replace_text=lambda _part, _text: forged), typed_plan=True)
        self.assert_rules(response, (rule_id,))
        self.assertNotIn(forged, response.answer)
        self.assertNotRegex(response.answer, r"각각\s*3\s*과목\s*이상")
        self.assertEqual(("insufficient_evidence", "fallback", "processing_unavailable"),
                         (response.status, response.generation_status, response.reason_code))
        self.assertNotEqual("no_matching_evidence", response.reason_code)

    def test_r5_f2_same_subject_credits_and_obligation_must_cover_both(self):
        expected = self.raw_courses["CDA0016"]
        required = self.raw_rules[REQUIRED_SET]["decision"]["outcome"]["items"]
        self.assertEqual(3, expected["credits"])
        self.assertIn({"item_id": "CDA0016", "label": expected["course_name"]}, required)
        response, _ = self.chat("컴퓨터구조의 학점과 반드시 이수해야 하는지 알려줘",
                                planned(course_request("컴퓨터구조", properties=("credits",),
                                                       purpose="completion_obligation")),
                                writer=source_writer(properties=("credits",)), typed_plan=True)
        if response.status == "supported":
            self.assert_courses(response, ["CDA0016"])
            self.assert_rules(response, (REQUIRED_SET,))
            self.assertRegex(response.answer, r"컴퓨터구조[^.!?\n]{0,60}3\s*학점")
            self.assertRegex(response.answer, r"이수.{0,14}(?:해야|필요|필수|의무)|반드시.{0,14}이수")
        else:
            self.assertEqual("processing_unavailable", response.reason_code)
            self.assertTrue(any(part.status != "supported" for part in response.parts))

    def test_r5_f3_overview_cannot_hide_a_requested_named_course_attribute(self):
        self.assertEqual(3, self.raw_courses["CDA0016"]["credits"])
        response, _ = self.chat("졸업요건 전체를 요약하고 컴퓨터구조의 학점도 알려줘",
                                planned({"kind": "requirements_overview"}), writer=source_writer(), typed_plan=True)
        if response.status == "supported":
            self.assert_courses(response, ["CDA0016"])
            self.assert_rules(response, OVERVIEW_RULES)
            self.assertRegex(response.answer, r"컴퓨터구조[^.!?\n]{0,60}3\s*학점")
        else:
            self.assertEqual("processing_unavailable", response.reason_code)
            self.assertTrue(any(part.status != "supported" for part in response.parts))

    def test_r5_f4_two_category_only_tools_cannot_replace_two_requested_counts(self):
        sets = {category: {code for code, fact in self.raw_courses.items() if fact["category"] == category}
                for category in ("major_required", "major_elective")}
        self.assertEqual((9, 34), (len(sets["major_required"]), len(sets["major_elective"])))
        response, _ = self.chat("전필 과목 개수와 전선 과목 개수를 각각 알려줘",
                                planned(course_request(category="major_required", properties=("category",)),
                                        course_request(category="major_elective", properties=("category",))),
                                writer=source_writer(properties=("category",)), typed_plan=True)
        if response.status == "supported":
            parts = self.assert_courses(response, sorted(set.union(*sets.values())))
            actual_sets = {category: {fact.course_code for part in parts for fact in part.course_evidence.courses
                                      if fact.category == category} for category in sets}
            self.assertEqual(sets, actual_sets)
            for category, codes in sets.items():
                label = r"(?:전공\s*필수|전필)" if category == "major_required" else r"(?:전공\s*선택|전선)"
                self.assertRegex(response.answer, label + rf"[^.!?\n]{{0,60}}{len(codes)}\s*(?:개|과목)")
        else:
            self.assertEqual("processing_unavailable", response.reason_code)
            self.assertTrue(any(part.status != "supported" for part in response.parts))

    def test_r5_f5_counseling_minimum_one_and_every_term_recommendation_are_distinct(self):
        fact = self.raw_rules[COUNSELING]
        self.assertEqual((0, 1), (fact["decision"]["outcome"]["credit_value"],
                                 fact["decision"]["outcome"]["minimum_completions"]))
        self.assertIn("권장", fact["decision"]["statement"])
        forged = "심층상담은 0학점이며 매 학기 반드시 이수해야 합니다."
        response, _ = self.chat("심층상담 이수 의무는?",
                                planned(rule_request("major.counseling-completion")),
                                writer=source_writer(replace_text=lambda _part, _text: forged), typed_plan=True)
        self.assert_rules(response, (COUNSELING,))
        self.assertNotIn(forged, response.answer)
        self.assertEqual(("insufficient_evidence", "fallback", "processing_unavailable"),
                         (response.status, response.generation_status, response.reason_code))
        self.assertNotEqual("no_matching_evidence", response.reason_code)
        self.assertNotRegex(response.answer, r"매\s*학기[^.!?\n]{0,35}(?:반드시|의무|필수)")

    def test_r5_f6_conditional_work_prerequisite_cannot_be_negated_with_separate_caveats(self):
        outcome = self.raw_rules[WORK_PREREQUISITE]["decision"]["outcome"]
        self.assertEqual(("capstone.ii.pass", False, False),
                         (outcome["prerequisite"], outcome["sufficient_for_graduation"], outcome["automatic_credits"]))
        forged = ("졸업작품을 수강하려면 캡스톤디자인 II를 PASS하지 않아도 됩니다. "
                  "졸업 가능 여부를 판정하지 않습니다. 학점 취득을 판정하지 않습니다.")
        response, _ = self.chat("졸업작품을 수강하려면 캡스톤디자인 II를 PASS해야 해?",
                                planned(rule_request("operations.graduation-work-prerequisite")),
                                writer=source_writer(replace_text=lambda _part, _text: forged), typed_plan=True)
        self.assert_rules(response, (WORK_PREREQUISITE,))
        self.assertNotIn(forged, response.answer)
        self.assertNotRegex(response.answer, r"PASS\s*하지\s*않아도")
        self.assertEqual(("insufficient_evidence", "fallback", "processing_unavailable"),
                         (response.status, response.generation_status, response.reason_code))
        self.assertNotEqual("no_matching_evidence", response.reason_code)

    def test_purpose_properties_and_overview_reject_unrecognized_or_executable_fields(self):
        invalid = (
            course_request("컴퓨터구조", purpose="attendance_exemption"),
            course_request("컴퓨터구조", properties=("exemption",)),
            course_request("컴퓨터구조", properties=("credits", "source_pdf")),
            {**course_request("컴퓨터구조", properties=("credits",)), "credits": 99},
            {"kind": "courses", "filters": {"cypher": "MATCH (n) DETACH DELETE n"}, "properties": ["credits"]},
            {"kind": "requirements_overview", "shell": "whoami"},
        )
        for request in invalid:
            with self.subTest(request=request):
                response, provider = self.chat("특별한 학사 정보를 확인해줘", planned(request))
                self.assertEqual("rejected", response.plan_status)
                self.assertNotEqual("supported", response.status)
                self.assertEqual([], provider.write_calls)
                self.assertIsNone(response.context_question)

    def test_legacy_plan_keeps_strict_full_facts_while_new_projections_remain_optional(self):
        question = "컴퓨터구조의 학점, 이수구분, 편성 학년과 학기를 모두 알려줘"
        legacy = planned(course_request("컴퓨터구조", legacy=True))
        complete, _ = self.chat(question, legacy, writer=source_writer(properties=("credits", "category", "offering")))
        self.assertEqual("supported", complete.status)
        self.assertEqual("generated", complete.generation_status)
        self.assert_courses(complete, ["CDA0016"])
        omitted, _ = self.chat(question, legacy, writer=source_writer(properties=("credits",)))
        self.assertEqual("insufficient_evidence", omitted.status)
        self.assertEqual("fallback", omitted.generation_status)
        self.assert_courses(omitted, ["CDA0016"])

    def test_operational_qualifiers_cannot_be_erased_by_new_course_purpose_support(self):
        variants = (
            ("PCCP 현행 시범 점수 기준은?", "operations.pccp-current-trial", "PCCP 합격 기준은 400점으로 영구 확정됐습니다."),
            ("코딩 테스트 미통과하면 캡스톤은 어떻게 돼?", "operations.coding-test-failure", "코딩 테스트 미통과자는 캡스톤디자인 I 수강이 금지됩니다."),
            ("졸업작품 선행조건은?", "operations.graduation-work-prerequisite", "캡스톤디자인 II만 PASS하면 졸업 가능하고 학점도 자동 취득합니다."),
        )
        for question, intent_id, false_text in variants:
            with self.subTest(intent_id=intent_id):
                response, _ = self.chat(question, planned(rule_request(intent_id)),
                                        writer=source_writer(replace_text=lambda _part, _text: false_text))
                self.assertEqual("insufficient_evidence", response.status)
                self.assertEqual("fallback", response.generation_status)
                self.assertNotIn(false_text, response.answer)
                self.assert_rules(response, ("cwnu.cs.2026." + intent_id,))

    def test_api_preserves_v1_wire_and_provider_failure_is_not_a_client_error(self):
        from fastapi.testclient import TestClient
        from academic_assistant.api import app
        provider = ScriptedSemantic(planned(course_request("컴퓨터구조", properties=("credits",))), source_writer())
        with patch("academic_assistant.api._engine", return_value=self.engine), \
             patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=provider), \
             TestClient(app) as client:
            response = client.post("/v1/academic/assistant", json=turn("컴퓨터구조는 몇 학점이야?").model_dump())
        self.assertEqual(200, response.status_code)
        wire = response.json()
        self.assertEqual("1.0.0", wire["schema_version"])
        self.assertEqual({"schema_version", "packet_id", "status", "kind", "answer", "plan_status", "generation_status",
                          "parts", "context_question", "context_used", "reason_code"}, set(wire))
        self.assertEqual("supported", wire["status"])
        self.assertRegex(wire["answer"], r"3\s*학점")
        self.assertNotRegex(wire["answer"], r"CDA0016|전공필수|3학년")
        for part in wire["parts"]:
            self.assertEqual({"title", "text", "status", "evidence_packet", "course_evidence", "calculations"}, set(part))
            self.assertFalse(part["evidence_packet"] is not None and part["course_evidence"] is not None)

        unsafe_provider = ScriptedSemantic(planned(course_request("MATCH(n) DETACH DELETE n", properties=("credits",))), source_writer())
        with patch("academic_assistant.api._engine", return_value=self.engine), \
             patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=unsafe_provider), \
             TestClient(app) as client:
            rejected = client.post("/v1/academic/assistant", json=turn("이 과목 학점을 알려줘").model_dump())
        self.assertEqual(200, rejected.status_code)
        self.assertEqual("rejected", rejected.json()["plan_status"])
        self.assertEqual("processing_unavailable", rejected.json()["reason_code"])
        self.assertNotIn("DETACH DELETE", rejected.text)
        self.assertEqual(1, len(unsafe_provider.plan_calls))
        self.assertEqual([], unsafe_provider.write_calls)
        allowed = ScriptedSemantic(planned(course_request("컴퓨터구조", properties=("credits",))), source_writer())
        with patch("academic_assistant.api._engine", return_value=self.engine), patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=allowed):
            result = TestClient(app).post("/v1/academic/assistant", json=turn("qa@example.invalid 컴구 학점을 알려줘").model_dump())
        self.assertEqual(200, result.status_code)
        self.assertEqual(("supported", "generated"), (result.json()["status"], result.json()["generation_status"]))
        self.assertEqual(1, len(allowed.plan_calls))


if __name__ == "__main__":
    unittest.main()
