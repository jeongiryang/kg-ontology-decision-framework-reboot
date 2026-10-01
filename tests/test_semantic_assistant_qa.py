"""Independent semantic-assistant QA; synthetic planner/writer, no live inference.

Course rows below are owned synthetic fixture data, not a source audit or actual
curriculum counts. Public academic rule expectations use the pinned real registry.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.assistant_models import AssistantTurnRequest, CourseEvidencePacket
from academic_assistant.core import AnswerEngine
from academic_assistant.llm import LLMInvalidResponse, LLMUnavailable
from academic_assistant.models import Issue
from academic_assistant.registry import Registry, canonical_sha256
from academic_assistant.transcript_models import TranscriptAssessmentRequest


SCREENSHOT_QUESTIONS = (
    "컴퓨터구조는 몇학점이야",
    "안녕",
    "3학년 과목 알려줘",
    "전선 과목 다 알려줘",
    "전필 과목 전선 과목 개수 몇 개야",
)

SYNTHETIC_COURSES = (
    ("qa.architecture", "CDA0016", "컴퓨터구조", "major_required", 3, 3, 1),
    ("qa.os", "CDA0017", "운영체제", "major_required", 3, 3, 2),
    ("qa.compiler", "QA303", "컴파일러", "major_elective", 3, 3, 2),
    ("qa.algorithm", "CDA0027", "알고리즘", "major_elective", 3, 2, 2),
)


def turn(question, **updates):
    value = dict(question=question, admission_year=2026, matched_curriculum_year=2026,
                 department="컴퓨터공학과", earned_credits={})
    value.update(updates)
    return AssistantTurnRequest.model_validate(value)


def current_record(*, credits=7, grade="A0", complete=True, confirmed=True):
    return TranscriptAssessmentRequest.model_validate(dict(
        admission_year=2026, matched_curriculum_year=2026, department="컴퓨터공학과",
        degree_track="single_major", confirmed=confirmed, record_complete=complete,
        courses=[dict(row_id="synthetic-row", course_code="QAPRIVATE0001", course_name="가상선택과목",
                      credits=credits, grade=grade, category="free")]))


class SyntheticCatalogue:
    """Trusted retrieval seam fixture; never touches files, Neo4j or a service."""
    def __init__(self):
        self.calls = []

    def __call__(self, engine, filters):
        filters = deepcopy(filters)
        self.calls.append(filters)
        facts, citations = [], []
        for course_id, code, name, category, credits, year, semester in SYNTHETIC_COURSES:
            values = dict(name=name, category=category, year=year, semester=semester)
            if any(value is not None and values.get(key) != value for key, value in filters.items()):
                continue
            fact = dict(course_id=course_id, course_code=code, course_name=name,
                        category=category, credits=credits, year=year, semester=semester,
                        offering_years=[year], offering_semesters=[str(semester)],
                        offering_label=f"{year}학년 {semester}학기",
                        theory=None, practical=None, minor_required=False,
                        source_id="qa.synthetic.course-source", source_sha256="a" * 64,
                        locator="synthetic catalogue row " + code)
            fact["fact_sha256"] = canonical_sha256(fact)
            facts.append(fact)
            citations.append(dict(course_id=course_id, source_id=fact["source_id"],
                                  source_sha256=fact["source_sha256"], locator=fact["locator"],
                                  claim=f"{name}: {credits}학점, {year}학년 {semester}학기, {category}"))
        return CourseEvidencePacket.model_validate(dict(
            schema_version="1.0.0", packet_id="qa-synthetic-catalogue",
            scope=dict(admission_year=2026, matched_curriculum_year=2026, department="컴퓨터공학과"),
            status="supported" if facts else "insufficient_evidence", courses=facts,
            evidence=citations, issues=[] if facts else [Issue(kind="missing", message="가상 목록에 해당 과목이 없습니다.")]))


def planned(*requests, context_used=False):
    return {"requests": list(requests), "context_used": context_used}


def rule(*intent_ids):
    return {"kind": "rule", "intent_ids": list(intent_ids)}


def courses(**filters):
    return {"kind": "courses", "filters": filters}


def natural_document(payload):
    """Independent public-fact writer, not a product permitted-text helper."""
    sections = []
    for part in payload["parts"]:
        sentences = ["핵심 내용을 차근차근 정리해 드리면 다음과 같아요."]
        facts = part["facts"]
        if facts and all("course_name" in fact for fact in facts):
            sentences.append(f"이 목록에는 과목이 총 {len(facts)}개 있어요.")
        for fact in facts:
            if "statement" in fact:
                sentences.append(fact["statement"])
                items = fact.get("outcome", {}).get("items", [])
                if items:
                    sentences.append("해당 항목은 " + ", ".join(item["label"] for item in items) + "입니다.")
            else:
                label = {"major_required": "전공필수", "major_elective": "전공선택"}[fact["category"]]
                particle = "은" if (ord(fact["course_name"][-1]) - 0xAC00) % 28 else "는"
                sentences.append(f"{fact['course_name']}{particle} {fact['credits']}학점인 {label} 과목이에요.")
                if fact.get("offering_label"):
                    sentences.append("원문 편성 표시는 " + fact["offering_label"] + "예요.")
                elif fact.get("year") is not None and fact.get("semester") is not None:
                    sentences.append(f"편성 학년과 학기는 {fact['year']}학년 {fact['semester']}학기예요.")
        sections.append({"part_id": part["part_id"], "text": " ".join(sentences),
                         "fact_ids": [fact["fact_id"] for fact in facts]})
    return {"sections": sections}


class ScriptedSemantic:
    """Exercise real consumer validation with untrusted documents, no provider."""
    def __init__(self, plan, writer=natural_document, *, cached=False):
        self.document = deepcopy(plan)
        self.writer = writer
        self.cached = cached
        self.plan_calls, self.write_calls = [], []
        self.entered, self.exited = 0, 0

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
        return SemanticDocument(document=deepcopy(self.document), cached=self.cached)

    def write(self, payload):
        from academic_assistant.semantic_llm import SemanticDocument
        self.write_calls.append(deepcopy(payload))
        if isinstance(self.writer, Exception):
            raise self.writer
        return SemanticDocument(document=self.writer(deepcopy(payload)), cached=self.cached)


class SemanticAssistantIndependentQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load(ROOT)
        cls.topics = json.loads((ROOT / "tests/fixtures/natural-conversation-evaluation.json").read_text(encoding="utf-8"))["topics"]

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ACADEMIC_LLM_PROVIDER": "disabled"}).start()
        self.catalogue = SyntheticCatalogue()
        self.engine = AnswerEngine(self.registry)

    def chat(self, question, plan, *, writer=natural_document, engine=None, cached=False, **updates):
        from academic_assistant.assistant import SemanticAssistant
        client = ScriptedSemantic(plan, writer, cached=cached)
        with patch("academic_assistant.assistant.retrieve_courses", self.catalogue):
            response = SemanticAssistant(engine=engine or self.engine, llm=client).chat(turn(question, **updates))
        self.assertEqual(client.entered, client.exited, "Owned semantic session must always release")
        self.assertLessEqual(len(client.plan_calls), 1)
        self.assertLessEqual(len(client.write_calls), 1)
        return response, client

    def assert_rule_authority(self, part):
        self.assertEqual("supported", part.status)
        packet = part.evidence_packet
        self.assertIsNotNone(packet)
        self.assertEqual("supported", packet.status)
        self.assertIsNone(part.course_evidence)
        for applied in packet.applied_rules:
            expected = self.registry.rules[applied.rule_id]
            self.assertEqual(self.registry.rule_hashes[applied.rule_id], applied.rule_sha256)
            self.assertEqual("approved", expected["review"]["status"])
            citations = [cite for cite in packet.evidence if cite.rule_id == applied.rule_id]
            self.assertTrue(citations)
            for cite in citations:
                self.assertEqual(expected["decision"]["statement"], cite.claim)
                self.assertIn((cite.source_id, cite.locator),
                              {(ref["source_id"], ref["locator"]) for ref in expected["evidence"]})

    def assert_truthful_fallback(self, response):
        self.assertEqual("fallback", response.generation_status)
        self.assertNotIn("QA_FORGED", response.answer)
        for part in response.parts:
            self.assertNotIn("QA_FORGED", part.text)

    def test_exact_five_screenshot_questions_at_real_assistant_boundary(self):
        expectations = (
            (SCREENSHOT_QUESTIONS[0], planned(courses(name="컴퓨터구조")), ["qa.architecture"]),
            (SCREENSHOT_QUESTIONS[1], planned({"kind": "greeting"}), []),
            (SCREENSHOT_QUESTIONS[2], planned(courses(year=3)), ["qa.architecture", "qa.os", "qa.compiler"]),
            (SCREENSHOT_QUESTIONS[3], planned(courses(category="major_elective")), ["qa.compiler", "qa.algorithm"]),
            (SCREENSHOT_QUESTIONS[4], planned(courses(category="major_required"), courses(category="major_elective")),
             ["qa.architecture", "qa.os", "qa.compiler", "qa.algorithm"]),
        )
        for question, plan, expected_ids in expectations:
            with self.subTest(question=question):
                response, client = self.chat(question, plan)
                self.assertEqual("generated", response.plan_status)
                self.assertEqual(1, len(client.plan_calls))
                if question == "안녕":
                    self.assertEqual("greeting", response.kind)
                    self.assertEqual("out_of_scope", response.status)
                    self.assertEqual([], response.parts)
                    self.assertTrue(response.answer)
                    self.assertEqual([], client.write_calls)
                    continue
                self.assertEqual("supported", response.status)
                self.assertEqual("academic", response.kind)
                self.assertEqual("generated", response.generation_status)
                self.assertEqual(expected_ids, [fact.course_id for part in response.parts for fact in part.course_evidence.courses])
                self.assertIn("차근차근", response.answer, "Actual writer prose must be used")
                for part in response.parts:
                    self.assertIsNone(part.evidence_packet)
                    self.assertEqual("supported", part.course_evidence.status)
                    for fact in part.course_evidence.courses:
                        self.assertIn(fact.course_name, part.text)
                        self.assertIn(f"{fact.credits}학점", part.text)
                if question == SCREENSHOT_QUESTIONS[4]:
                    self.assertEqual(2, len(response.parts))
                    self.assertTrue(all("2개" in part.text for part in response.parts))

    def test_course_credit_paraphrases_are_not_a_five_question_whitelist(self):
        for question in ("컴구가 3학점인지 확인해서 알려줄래요?", "컴퓨터 구조 과목의 학점 수가 궁금해요.",
                         "CDA0016을 듣게 되면 과목 자체는 몇 학점인가요?"):
            with self.subTest(question=question):
                response, _ = self.chat(question, planned(courses(name="컴퓨터구조")))
                self.assertEqual("supported", response.status)
                self.assertEqual("generated", response.generation_status)
                self.assertIn("컴퓨터구조", response.answer)
                self.assertIn("3학점", response.answer)

    def test_additional_named_course_category_and_term_questions(self):
        for question, name, expected in (
            ("운영체제는 몇학점이야?", "운영체제", ("3학점",)),
            ("컴퓨터구조는 전필이야 전선이야?", "컴퓨터구조", ("전공필수",)),
            ("알고리즘은 몇학년 몇학기에 들어?", "알고리즘", ("2학년", "2학기")),
        ):
            with self.subTest(question=question):
                response, _ = self.chat(question, planned(courses(name=name)))
                self.assertEqual("supported", response.status)
                self.assertEqual("generated", response.generation_status)
                for value in expected:
                    self.assertIn(value, response.answer)

    def test_real_source_approved_architecture_code_credits_and_documented_aliases(self):
        from academic_assistant.courses import retrieve_courses
        for name in ("컴퓨터구조", "컴퓨터 구조", "컴구", "CDA0016"):
            with self.subTest(name=name):
                packet = retrieve_courses(self.engine, {"name": name})
                self.assertEqual("supported", packet.status)
                self.assertEqual(1, len(packet.courses))
                fact = packet.courses[0]
                self.assertEqual(("CDA0016", "컴퓨터구조", 3), (fact.course_code, fact.course_name, fact.credits))
                self.assertEqual(self.registry.sources[fact.source_id]["sha256"], fact.source_sha256)
                self.assertEqual("approved", self.registry.sources[fact.source_id]["review"]["status"])
                self.assertEqual(fact.fact_sha256, canonical_sha256(fact.model_dump(exclude={"fact_sha256"})))
                self.assertIn("PDF", fact.locator)
                self.assertEqual(fact.course_id, packet.evidence[0].course_id)
                self.assertIn("3학점", packet.evidence[0].claim)

    def test_all31_approved_topics_survive_semantic_not_fullmatch_wording(self):
        self.assertEqual(31, len(self.topics))
        for topic in self.topics:
            question = topic["questions"][0] + " 저는 그 내용을 쉬운 말로 이해하고 싶어요."
            with self.subTest(intent=topic["intent_id"]):
                response, client = self.chat(question, planned(rule(topic["intent_id"])))
                self.assertEqual("generated", response.plan_status)
                self.assertEqual("supported", response.status)
                self.assertTrue(response.parts)
                self.assertEqual(1, len(client.plan_calls))
                for part in response.parts:
                    self.assert_rule_authority(part)

    def test_writer_accepts_genuine_new_minimum_paraphrase_not_enumerated_template(self):
        prose = "졸업 총학점은 130학점 이상 채우셔야 해요. 이 수치는 최소 이수 기준을 뜻합니다."
        def writer(payload):
            part = payload["parts"][0]
            return {"sections": [{"part_id": part["part_id"], "text": prose,
                                   "fact_ids": [fact["fact_id"] for fact in part["facts"]]}]}
        response, _ = self.chat("졸업할 때 채워야 하는 학점 합계부터 쉽게 설명해 주세요.",
                                planned(rule("credits.graduation.total")), writer=writer)
        self.assertEqual("generated", response.generation_status)
        self.assertIn(prose, response.answer)
        self.assert_rule_authority(response.parts[0])

    def test_supported_subquestion_survives_an_ambiguous_extra_part(self):
        response, _ = self.chat("졸업 총학점은 알려주고, 제 장학금도 받을 수 있는지 알려주세요.",
                                planned(rule("credits.graduation.total"), {"kind": "clarify"}))
        self.assertNotEqual("supported", response.status)
        supported = [part for part in response.parts if part.status == "supported"]
        self.assertTrue(supported, "Approved evidence is independently usable despite an extra unclear request")
        self.assert_rule_authority(supported[0])
        self.assertIn("130", response.answer)
        self.assertTrue(any(part.status != "supported" for part in response.parts))

    def test_compound_current_credits_are_deterministic_not_writer_supplied(self):
        response, client = self.chat("졸업 총학점이랑 교양 총학점 각각 얼마나 더 채우면 되나요?",
                                    planned(rule("credits.graduation.total", "credits.general.total")),
                                    earned_credits={"credits.graduation.total": 107, "credits.general.total": 12})
        self.assertEqual("supported", response.status)
        values = {calc.metric: (calc.required, calc.earned, calc.gap) for part in response.parts for calc in part.calculations}
        self.assertEqual({"credits.graduation.total": (130, 107, 23), "credits.general.total": (34, 12, 22)}, values)
        for part in response.parts:
            self.assert_rule_authority(part)
        self.assertNotIn("earned_credits", json.dumps(client.write_calls, ensure_ascii=False))

    def test_ambiguity_and_unknown_course_do_not_borrow_supported_context(self):
        for question, plan in (("그럼 몇 개예요?", planned({"kind": "clarify"})),
                               ("등록되지 않은 특별강의는 몇 학점이에요?", planned(courses(name="미등록특별강의")))):
            with self.subTest(question=question):
                response, client = self.chat(question, plan)
                self.assertEqual("insufficient_evidence", response.status)
                self.assertIsNone(response.context_question)
                self.assertFalse(any(part.status == "supported" for part in response.parts))
                self.assertEqual([], client.write_calls)

    def test_missing_source_and_processing_outage_have_distinct_reason_codes(self):
        empty, _ = self.chat("확인되지 않은 특별강의는 몇 학점인가요?",
                             planned(courses(name="확인되지않은특별강의")))
        self.assertEqual("insufficient_evidence", empty.status)
        self.assertEqual("no_matching_evidence", empty.reason_code)
        unavailable, _ = self.chat("처음 보는 학사 내용을 확인해 주세요", LLMUnavailable("synthetic outage"))
        self.assertEqual("processing_unavailable", unavailable.reason_code)
        missing_record, _ = self.chat("현재 기록으로 부족 학점을 비교해 주세요",
                                      planned({"kind": "transcript", "topic": "graduation_credits"}))
        self.assertEqual("missing_student_input", missing_record.reason_code)
        self.assertNotEqual("supported", missing_record.status)

    def test_multiple_offering_years_are_not_reported_as_unknown(self):
        catalogue = self.catalogue
        def multi_year(engine, filters):
            packet = catalogue(engine, filters)
            values = packet.model_dump()
            fact = values["courses"][0]
            fact.update(year=None, semester=None, offering_years=[2, 3],
                        offering_semesters=["1", "2"], offering_label="2·3학년 1·2학기")
            fact["fact_sha256"] = canonical_sha256({key: value for key, value in fact.items() if key != "fact_sha256"})
            values["evidence"][0]["claim"] = "컴퓨터구조: 3학점, 전공필수, 2·3학년 1·2학기"
            return CourseEvidencePacket.model_validate(values)
        self.catalogue = multi_year
        response, _ = self.chat("컴퓨터구조 편성 학년과 학기를 알려주세요", planned(courses(name="컴퓨터구조")))
        self.assertEqual("supported", response.status)
        self.assertEqual("generated", response.generation_status)
        self.assertIn("2·3학년 1·2학기", response.answer)
        self.assertNotIn("미확인", response.answer)
        self.assertEqual([2, 3], response.parts[0].course_evidence.courses[0].offering_years)

    def test_unknown_personal_conditions_do_not_become_approval_from_valid_rule_id(self):
        for question in ("제가 복학생인데 전공필수 과목을 자동으로 면제받나요?",
                         "PCCP를 통과하면 제 캡스톤도 자동으로 Pass인가요?",
                         "졸업 총학점의 최대 제한이 130학점이라는 뜻인가요?"):
            with self.subTest(question=question):
                response, _ = self.chat(question, planned(rule("credits.graduation.total")))
                self.assertNotEqual("supported", response.status)
                self.assertIsNone(response.context_question)
                self.assertTrue(response.kind in {"clarification", "refusal"}
                                or any(part.status != "supported" for part in response.parts))

    def test_scope_rejected_before_any_semantic_provider_call(self):
        from academic_assistant.assistant import SemanticAssistant
        for updates in ({"admission_year": 2025}, {"matched_curriculum_year": 2027}, {"department": "간호학과"}):
            client = ScriptedSemantic(planned(rule("credits.graduation.total")))
            response = SemanticAssistant(engine=self.engine, llm=client).chat(turn("졸업 총학점이 궁금해요", **updates))
            self.assertEqual("out_of_scope", response.status)
            self.assertEqual([], client.plan_calls)
            self.assertEqual([], client.write_calls)

    def test_known_pii_in_current_or_previous_question_never_reaches_model(self):
        from academic_assistant.assistant import SemanticAssistant
        for private in ("학번 2026123456 졸업학점", "홍길동은 졸업학점이 궁금해요", "qa@example.invalid 졸업학점",
                        "010-1234-5678 졸업학점", "raw transcript 졸업학점"):
            for anchored in (False, True):
                with self.subTest(anchored=anchored, private=private):
                    client = ScriptedSemantic(planned(rule("credits.graduation.total")))
                    payload = turn("다시 쉽게 설명해 주세요" if anchored else private,
                                   previous_question=private if anchored else None)
                    with self.assertRaises(ValueError):
                        SemanticAssistant(engine=self.engine, llm=client).chat(payload)
                    self.assertEqual([], client.plan_calls)
                    self.assertEqual([], client.write_calls)

    def test_plan_json_allowlists_reject_untrusted_facts_and_executable_queries(self):
        invalid = (
            planned(rule("invented.unapproved.rule")),
            planned({"kind": "rule", "intent_ids": ["credits.graduation.total"], "credits": 1}),
            planned(courses(category="all")), planned(courses(year=True)),
            planned({"kind": "courses", "filters": {"cypher": "MATCH (n) DETACH DELETE n"}}),
            planned({"kind": "transcript", "topic": "final_graduation"}),
            planned(*[{"kind": "greeting"}] * 5),
            {"requests": [{"kind": "greeting"}], "context_used": False, "shell": "whoami"},
        )
        for plan in invalid:
            with self.subTest(plan=plan):
                response, client = self.chat("특별한 학사 내용이 궁금합니다", plan)
                self.assertEqual("rejected", response.plan_status)
                self.assertNotEqual("supported", response.status)
                self.assertEqual([], client.write_calls)
                self.assertIsNone(response.context_question)

    def test_planner_and_writer_outages_have_truthful_non_model_fallback(self):
        response, client = self.chat("처음 보는 이수 조건이 궁금해요", LLMUnavailable("C:/private/credentials.json"))
        self.assertEqual("unavailable", response.plan_status)
        self.assertNotEqual("supported", response.status)
        self.assertNotIn("private", response.answer)
        self.assertEqual([], client.write_calls)
        response, _ = self.chat("졸업 총학점 설명 부탁드려요", planned(rule("credits.graduation.total")),
                                writer=LLMUnavailable("C:/private/student.json"))
        self.assertEqual("supported", response.status)
        self.assert_truthful_fallback(response)
        self.assertNotIn("private", response.answer)
        self.assert_rule_authority(response.parts[0])

    def test_writer_number_comparator_unapproved_date_and_final_verdict_mutations(self):
        mutations = (
            "졸업 총학점은 최소 129학점입니다.",
            "졸업 총학점은 최대 130학점입니다.",
            "졸업 총학점은 최소 130학점이고 2027년부터 120학점입니다.",
            "졸업 총학점은 최소 130학점이며 이 기준만 맞추면 졸업 가능합니다.",
            "졸업 총학점은 최소 130학점이며 근거는 PDF999쪽에 있습니다.",
        )
        for prose in mutations:
            with self.subTest(prose=prose):
                def mutate(payload):
                    document = natural_document(payload)
                    document["sections"][0]["text"] = prose
                    return document
                response, _ = self.chat("졸업 총학점 기준을 알기 쉽게 설명해요", planned(rule("credits.graduation.total")), writer=mutate)
                self.assertEqual("supported", response.status)
                self.assert_truthful_fallback(response)
                self.assert_rule_authority(response.parts[0])

    def test_writer_fact_ids_section_identity_and_forged_citation_cannot_display(self):
        def variants(payload):
            original = natural_document(payload)
            cases = []
            for ids in ([], ["invented"], [original["sections"][0]["fact_ids"][0]] * 2,
                        list(reversed(original["sections"][0]["fact_ids"]))):
                document = deepcopy(original); document["sections"][0]["fact_ids"] = ids; cases.append(document)
            document = deepcopy(original); document["sections"][0]["part_id"] = "p999"; cases.append(document)
            document = deepcopy(original); document["sections"][0]["citation"] = "QA_FORGED PDF999쪽"; cases.append(document)
            document = deepcopy(original); document["sections"][0]["text"] = "QA_FORGED"; cases.append(document)
            return cases
        for index in range(7):
            def mutate(payload):
                return variants(payload)[index]
            with self.subTest(index=index):
                response, _ = self.chat("졸업 총학점이랑 교양 총학점 기준을 비교해 주세요",
                                        planned(rule("credits.graduation.total", "credits.general.total")), writer=mutate)
                self.assert_truthful_fallback(response)
                for part in response.parts:
                    self.assert_rule_authority(part)

    def test_cached_labels_never_masquerade_as_fresh_generation(self):
        response, client = self.chat("전공 최소 학점을 쉽게 설명해 주세요",
                                    planned(rule("credits.major.minimum")), cached=True)
        self.assertEqual("cached", response.plan_status)
        self.assertEqual("cached", response.generation_status)
        self.assertEqual("supported", response.status)
        self.assertEqual(1, len(client.plan_calls))
        self.assertEqual(1, len(client.write_calls))
        self.assert_rule_authority(response.parts[0])

    def test_rule_subject_swaps_and_negated_requirements_cannot_display(self):
        cases = (
            ("교양 총학점과 전공필수 학점 기준을 같이 알려주세요",
             ("credits.general.total", "credits.major.required"),
             "교양 총학점은 최소 21학점, 전공필수는 최소 34학점 이수해야 합니다."),
            ("졸업논문 이수 요건을 알려주세요", ("graduation.thesis.required",),
             "졸업논문은 필수가 아닙니다."),
            ("졸업 총학점의 최소 기준을 알려주세요", ("credits.graduation.total",),
             "총 130학점 이상 채울 필요가 없습니다."),
            ("졸업 총학점의 최소 기준을 알려주세요", ("credits.graduation.total",),
             "졸업 총학점은 130학점 이상 이수해서는 안됩니다."),
            ("졸업논문 이수 요건을 알려주세요", ("graduation.thesis.required",),
             "졸업논문은 필수이며 이수하지 마세요."),
        )
        for question, intents, false_text in cases:
            def malicious_writer(payload):
                document = natural_document(payload)
                document["sections"][0]["text"] = false_text
                return document
            with self.subTest(false_claim=false_text):
                response, _ = self.chat(question, planned(rule(*intents)), writer=malicious_writer)
                self.assertEqual("supported", response.status)
                self.assert_truthful_fallback(response)
                self.assertNotIn(false_text, response.answer)
                for part in response.parts:
                    self.assert_rule_authority(part)

    def test_source_course_category_term_and_extra_claim_mutations_cannot_display(self):
        from academic_assistant.courses import retrieve_courses
        self.catalogue = retrieve_courses
        cases = (
            ("전선 과목을 모두 알려주세요", courses(category="major_elective"),
             "위 34개 과목은 모두 전공필수입니다."),
            ("모바일프로그래밍 편성 학기를 알려주세요", courses(name="모바일프로그래밍"),
             "모바일프로그래밍은 첫 학기에 듣는 과목입니다."),
            ("컴퓨터구조의 이수구분을 알려주세요", courses(name="컴퓨터구조"),
             "컴퓨터구조는 전공선택이 아니라 전공필수입니다."),
            ("컴퓨터구조 과목의 학점을 알려주세요", courses(name="컴퓨터구조"),
             "양자역학도 전공선택 3학점입니다."),
            ("전선 과목을 모두 알려주세요", courses(category="major_elective"),
             "모든 과목은 3학점입니다."),
            ("모바일프로그래밍 편성 학기를 알려주세요", courses(name="모바일프로그래밍"),
             "양자역학: 전공선택 3학점입니다."),
        )
        for question, request, false_text in cases:
            def malicious_writer(payload):
                document = natural_document(payload)
                document["sections"][0]["text"] += " " + false_text
                return document
            with self.subTest(false_claim=false_text):
                response, _ = self.chat(question, planned(request), writer=malicious_writer)
                self.assertEqual("supported", response.status)
                self.assert_truthful_fallback(response)
                self.assertNotIn(false_text, response.answer)
                packet = response.parts[0].course_evidence
                self.assertEqual("supported", packet.status)
                if request["filters"].get("category") == "major_elective":
                    self.assertEqual(34, len(packet.courses))
                    self.assertTrue(all(fact.category == "major_elective" for fact in packet.courses))
                if request["filters"].get("name") == "모바일프로그래밍":
                    self.assertEqual((2, 2), (packet.courses[0].year, packet.courses[0].semester))

    def test_two_policy_subjects_cannot_be_fully_answered_by_one_valid_rule(self):
        response, _ = self.chat("교양 총학점과 전공필수 학점 기준을 함께 알려주세요",
                                planned(rule("credits.general.total")))
        self.assertNotEqual("supported", response.status)
        self.assertIsNone(response.context_question)
        self.assertNotEqual("no_matching_evidence", response.reason_code,
                            "A dropped approved subject is a processing gap, not missing academic evidence")
        supported = [part for part in response.parts if part.status == "supported"]
        self.assertTrue(supported, "The separately approved general-credit answer may be preserved")
        self.assertIn("34", response.answer)
        for part in supported:
            self.assert_rule_authority(part)

    def test_two_named_courses_cannot_be_fully_answered_by_one_valid_tool_result(self):
        from academic_assistant.courses import retrieve_courses
        self.catalogue = retrieve_courses
        response, _ = self.chat("논리설계와 시스템프로그래밍 두 과목은 각각 몇 학점인가요?",
                                planned(courses(name="논리설계")))
        self.assertNotEqual("supported", response.status)
        self.assertIsNone(response.context_question)
        self.assertTrue(response.reason_code in {"processing_unavailable", "ambiguous"})

    def test_course_alias_cannot_be_misrouted_to_supported_graduation_threshold(self):
        from academic_assistant.courses import retrieve_courses
        self.catalogue = retrieve_courses
        response, _ = self.chat("컴구는 몇 학점인가요?", planned(rule("credits.graduation.total")))
        self.assertNotEqual("supported", response.status)
        self.assertIsNone(response.context_question)
        self.assertNotEqual("generated", response.generation_status)

    def test_unsafe_provider_filter_is_rejected_as_provider_error_not_client_pii(self):
        response, client = self.chat("이 과목의 학점을 확인해 주세요", planned(courses(name="qa@example.invalid")))
        self.assertEqual("rejected", response.plan_status)
        self.assertEqual("processing_unavailable", response.reason_code)
        self.assertNotEqual("supported", response.status)
        self.assertIsNone(response.context_question)
        self.assertNotIn("qa@example.invalid", response.answer)
        self.assertEqual(1, len(client.plan_calls))
        self.assertEqual([], client.write_calls)
        # Observe the actual API classification as well as the facade: model
        # output is not a malformed user request, while user PII remains 422.
        from fastapi.testclient import TestClient
        from academic_assistant.api import app
        provider = ScriptedSemantic(planned(courses(name="qa@example.invalid")))
        with patch("academic_assistant.api._engine", return_value=self.engine), \
             patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=provider), \
             TestClient(app) as api_client:
            safe = api_client.post("/v1/academic/assistant", json=turn("이 과목의 학점을 확인해 주세요").model_dump())
            self.assertEqual(200, safe.status_code)
            self.assertEqual("rejected", safe.json()["plan_status"])
            self.assertEqual("processing_unavailable", safe.json()["reason_code"])
            self.assertNotIn("qa@example.invalid", safe.text)
            unsafe = api_client.post("/v1/academic/assistant", json=turn("qa@example.invalid 학점이 궁금해요").model_dump())
            self.assertEqual(422, unsafe.status_code)
            self.assertEqual({"detail": "invalid request"}, unsafe.json())
            self.assertEqual(1, len(provider.plan_calls), "Unsafe user input must not cause another provider call")

    def test_course_writer_cannot_change_name_credit_or_attribute_associations(self):
        for replacement in ("컴퓨터구조는 4학점입니다.", "양자역학은 3학점입니다.",
                            "컴퓨터구조는 전공선택 3학점입니다.", "컴퓨터구조는 4학년 1학기 3학점입니다."):
            def mutate(payload):
                document = natural_document(payload); document["sections"][0]["text"] = replacement
                return document
            with self.subTest(replacement=replacement):
                response, _ = self.chat("컴퓨터구조 과목 학점을 확인해 주세요", planned(courses(name="컴퓨터구조")), writer=mutate)
                self.assertEqual("supported", response.status)
                self.assert_truthful_fallback(response)
                self.assertEqual(3, response.parts[0].course_evidence.courses[0].credits)

    def test_operational_writer_cannot_erase_trial_or_essential_failure_caveats(self):
        cases = (
            ("operations.pccp-current-trial", "PCCP 현재 점수가 궁금해요", "PCCP 합격 기준은 400점입니다. 영구 확정된 정책입니다."),
            ("operations.coding-test-failure", "코딩시험을 통과 못하면 어떤가요", "코딩시험에 미통과하면 캡스톤디자인 I과 II 모두 자유롭게 수강할 수 있습니다."),
        )
        for intent, question, prose in cases:
            def mutate(payload):
                document = natural_document(payload); document["sections"][0]["text"] = prose
                return document
            with self.subTest(intent=intent):
                response, _ = self.chat(question, planned(rule(intent)), writer=mutate)
                self.assert_truthful_fallback(response)
                self.assert_rule_authority(response.parts[0])

    def test_conflict_does_not_become_supported_by_a_valid_plan_or_previous_anchor(self):
        conflicted = replace(self.registry, conflicts={"credit_threshold:credits.graduation.total": ["left", "right"]})
        response, client = self.chat("그 기준을 다시 쉽게 설명해 주세요", planned(rule("credits.graduation.total"), context_used=True),
                                    engine=AnswerEngine(conflicted), previous_question="졸업 총학점 기준")
        self.assertEqual("conflict", response.status)
        self.assertIsNone(response.context_question)
        self.assertEqual([], client.write_calls)

    def test_transcript_current_replay_and_provider_payload_privacy(self):
        first, first_client = self.chat("확인한 현재 기록에서 졸업까지 몇 학점이 더 필요해요?",
                                      planned({"kind": "transcript", "topic": "graduation_credits"}), transcript=current_record(credits=7))
        self.assertEqual("supported", first.status)
        self.assertTrue(first.context_question)
        second, second_client = self.chat("그 부족분을 다시 설명해 주세요",
                                        planned({"kind": "transcript", "topic": "graduation_credits"}, context_used=True),
                                        previous_question=first.context_question, transcript=current_record(credits=20))
        self.assertEqual("supported", second.status)
        self.assertTrue(second.context_used)
        text = second.answer + " ".join(part.text for part in second.parts)
        self.assertIn("110", text)
        self.assertNotIn("123학점", text)
        for client in (first_client, second_client):
            plan_wire = json.dumps(client.plan_calls, ensure_ascii=False)
            writer_wire = json.dumps(client.write_calls, ensure_ascii=False)
            for wire in (plan_wire, writer_wire):
                for forbidden in ("synthetic-row", "QAPRIVATE0001", "가상선택과목", "earned_credits", "student_facts", "record_complete"):
                    self.assertNotIn(forbidden, wire)
            self.assertNotIn("previous_question", writer_wire)
        self.assertIn("최종", text)

    def test_transcript_incomplete_failed_and_unconfirmed_rows_never_become_final_pass(self):
        for record in (current_record(grade="F"), current_record(complete=False)):
            with self.subTest(complete=record.record_complete, grade=record.courses[0].grade):
                response, _ = self.chat("현재 기록에서 얼마나 졸업 학점이 부족한가요?",
                                        planned({"kind": "transcript", "topic": "graduation_credits"}), transcript=record)
                if record.record_complete:
                    self.assertIn("130", response.answer)
                else:
                    self.assertNotEqual("supported", response.status)
                    self.assertIsNone(response.context_question)
                self.assertNotIn("최종 졸업 가능", response.answer)
        from academic_assistant.assistant import SemanticAssistant
        client = ScriptedSemantic(planned({"kind": "transcript", "topic": "graduation_credits"}))
        with self.assertRaises(ValueError):
            SemanticAssistant(engine=self.engine, llm=client).chat(turn("현재 기록의 부족학점", transcript=current_record(confirmed=False)))
        self.assertEqual([], client.plan_calls)


class SemanticProviderBudgetIndependentQA(unittest.TestCase):
    """Exercise the actual request/session boundary, with no socket or provider."""

    def setUp(self):
        from academic_assistant import llm
        from academic_assistant.semantic_llm import SemanticLLMClient
        self.llm = llm
        self.clock = [100.0]
        self.budget = llm._RequestBudget()
        self.settings = llm.LLMSettings(provider="ollama", base_url="http://127.0.0.1:9999",
                                        model="synthetic-qa", timeout_seconds=30,
                                        max_response_bytes=1024, min_interval_seconds=60)
        self.client = SemanticLLMClient(self.settings, interval_seconds=2)
        self.addCleanup(patch.stopall)
        patch("academic_assistant.llm._budget", self.budget).start()
        patch("academic_assistant.semantic_llm.time.monotonic", side_effect=lambda: self.clock[0]).start()

    @staticmethod
    def envelope(document):
        return json.dumps({"done": True, "message": {"role": "assistant", "content": json.dumps(document)}}, ensure_ascii=False).encode("utf-8")

    def test_plan_and_writer_hold_one_shared_slot_without_reset_or_cache(self):
        from academic_assistant.semantic_llm import SemanticLLMClient
        calls = []
        def post(request, remaining):
            body = json.loads(request.data)
            calls.append((body, remaining))
            self.assertTrue(self.budget.active)
            self.assertEqual("http://127.0.0.1:9999/api/chat", request.full_url)
            self.assertEqual(["system", "user"], [message["role"] for message in body["messages"]])
            self.assertEqual({"untrusted_data"}, set(json.loads(body["messages"][1]["content"])))
            self.assertNotIn("prompt", body)
            self.assertNotIn("context", body)
            self.assertIs(False, body["stream"])
            self.assertIs(False, body["think"])
            self.assertEqual(512, body["options"]["num_predict"])
            return self.envelope({"requests": [{"kind": "greeting"}], "context_used": False} if len(calls) == 1 else {"sections": []})
        with patch.object(self.client, "_post", side_effect=post):
            with self.client.session() as session:
                session.plan({"question": "안녕", "has_transcript": False})
                with self.assertRaises(self.llm.LLMBusy):
                    with SemanticLLMClient(self.settings).session():
                        self.fail("Second semantic session must not enter")
                legacy = self.llm.LocalLLMClient(self.settings)
                # This assertion concerns a fresh inference, not legitimate
                # reuse of public legacy intent data. Clear only our isolated
                # per-test budget fixture, never the process product budget.
                self.assertIs(self.budget, self.llm._budget)
                with self.budget.lock:
                    self.budget.cache.clear()
                with patch.object(legacy, "_request_intent", side_effect=AssertionError("Legacy provider must not be reached")) as legacy_transport:
                    self.assertIsNone(legacy.suggest_intent("graduation", {"credits.graduation.total": "졸업 총학점"}))
                    legacy_transport.assert_not_called()
                    self.assertTrue(self.budget.active, "Legacy busy fallback must not release the semantic slot")
                self.clock[0] = 104.0
                session.write({"version": "1.0.0", "parts": []})
                with self.assertRaises(self.llm.LLMUnavailable):
                    session.write({"version": "1.0.0", "parts": []})
                self.assertTrue(self.budget.active)
                session.accept()
        self.assertEqual([30.0, 26.0], [remaining for _, remaining in calls])
        self.assertFalse(self.budget.active)
        self.assertEqual(106.0, self.budget.next_allowed)
        self.assertEqual(60, self.settings.min_interval_seconds, "Interactive policy must not mutate legacy settings")
        self.assertEqual({}, self.budget.cache)
        self.assertEqual({}, self.budget.generation_cache)
        with self.assertRaises(self.llm.LLMBusy):
            with self.client.session():
                self.fail("Successful interactive cooldown must be enforced")

    def test_elapsed_first_stage_consumes_absolute_turn_deadline(self):
        remaining_times = []
        def post(request, remaining):
            remaining_times.append(remaining)
            self.clock[0] += 21.0 if len(remaining_times) == 1 else 10.0
            return self.envelope({"requests": [{"kind": "greeting"}], "context_used": False})
        with patch.object(self.client, "_post", side_effect=post):
            with self.client.session() as session:
                session.plan({"question": "안녕", "has_transcript": False})
                with self.assertRaises(self.llm.LLMUnavailable):
                    session.write({"version": "1.0.0", "parts": []})
        self.assertEqual([30.0, 9.0], remaining_times)
        self.assertFalse(self.budget.active)
        self.assertEqual(191.0, self.budget.next_allowed)

    def test_provider_bounds_invalid_framing_and_failure_cleanup(self):
        cases = (
            (b"x" * 1025, self.llm.LLMUnavailable),
            (b'{"done":false,"message":{"content":"{}"}}', self.llm.LLMInvalidResponse),
            (b'{"done":true,"done":false,"message":{"content":"{}"}}', self.llm.LLMInvalidResponse),
            (json.dumps({"done": True, "message": {"role": "assistant", "content": '{"requests":[],"requests":[]}'}}).encode(), self.llm.LLMInvalidResponse),
            (b'{"done":true,"response":"{}"}', self.llm.LLMInvalidResponse),
        )
        for raw, error in cases:
            with self.subTest(replacement=raw[:80]):
                self.budget.next_allowed = 0
                with patch.object(self.client, "_post", return_value=raw) as transport:
                    with self.client.session() as session:
                        with self.assertRaises(error):
                            session.plan({"question": "안녕", "has_transcript": False})
                self.assertEqual(1, transport.call_count)
                self.assertFalse(self.budget.active)
                self.assertEqual(160.0, self.budget.next_allowed)


if __name__ == "__main__":
    unittest.main()
