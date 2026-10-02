"""Independent source-key QA at real B facade, API and public ASGI boundaries.

The provider is controlled, not live. Tests must run only on the parent's
frozen product. Public repair18 are committed; unused14 remain private until
final freeze. No implementation helper is an expected-answer oracle.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from academic_assistant.assistant import SemanticAssistant
from academic_assistant.assistant_models import AssistantTurnRequest, AssistantTurnResponse
from academic_assistant.core import AnswerEngine
from academic_assistant.registry import Registry
from academic_assistant.semantic_llm import SemanticDocument

FIXTURE = ROOT / "tests/fixtures/b-dialogue-v2-20261002.json"
SCOPE = dict(admission_year=2026, matched_curriculum_year=2026, department="컴퓨터공학과")
LABELS = {"major_required": "전공필수", "major_elective": "전공선택"}


def courses(name=None, properties=("credits",), purpose="attributes", **filters):
    return dict(kind="courses", filters={**({"name": name} if name else {}), **filters},
                purpose=purpose, properties=list(properties))


def planned(*requests, context_used=False, coverage=None, **extra):
    return dict(requests=list(requests), context_used=context_used,
                coverage=deepcopy(list(requests) if coverage is None else coverage), **extra)


def prose(payload):
    """Untrusted writer based on retrieved payload, never the expectation key."""
    sections = []
    for part in payload["parts"]:
        facts = part["facts"]
        properties = set(part.get("properties", []))
        summary = part.get("course_summary")
        clauses = []
        if summary is not None:
            label = LABELS.get(summary["filters"].get("category"), "조회한")
            clauses.append(f"{label} 목록에는 {summary['course_count']}과목이 있습니다.")
        for fact in facts:
            if "statement" in fact:
                clauses.append(fact["statement"])
                continue
            if "names" in properties:
                clauses.append(fact["course_name"] + ".")
            for prop in ("credits", "category", "offering", "code"):
                if prop not in properties:
                    continue
                value = {"credits": str(fact.get("credits")) + "학점",
                         "category": LABELS.get(fact.get("category"), ""),
                         "offering": str(fact.get("offering_label", "")) + " 편성",
                         "code": fact.get("code", "")}[prop]
                clauses.append(f"{fact['course_name']}는 {value}입니다.")
        sections.append(dict(part_id=part["part_id"], text=" ".join(clauses),
                             fact_ids=[fact["fact_id"] for fact in facts]))
    return dict(sections=sections)


class TypedProvider:
    typed_plans = True

    def __init__(self, document, writer=prose):
        self.document, self.writer = deepcopy(document), writer
        self.plans, self.writes = [], []
        self.entered = self.exited = 0
        self.accepted = False

    @contextmanager
    def session(self):
        self.entered += 1
        try:
            yield self
        finally:
            self.exited += 1

    def plan(self, payload):
        self.plans.append(deepcopy(payload))
        return SemanticDocument(deepcopy(self.document), typed_plan=True)

    def write(self, payload):
        self.writes.append(deepcopy(payload))
        return SemanticDocument(self.writer(deepcopy(payload), len(self.writes)) if callable(getattr(self.writer, "retry_writer", None))
                                else self.writer(deepcopy(payload)))

    def rewrite(self, payload, failed, error_code):
        # Echo the real product-provided failure, not an invented cause.
        return self.write({**deepcopy(payload), "correction": dict(error_code=error_code,
                            previous_document=deepcopy(failed.document) if isinstance(failed, SemanticDocument) else None)})

    def accept(self):
        self.accepted = True


class BDialogueSourceQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.registry = Registry.load(ROOT)
        cls.engine = AnswerEngine(cls.registry)

    def setUp(self):
        self.env = patch.dict(os.environ, {"ACADEMIC_LLM_PROVIDER": "disabled", "ACADEMIC_EVIDENCE_BACKEND": "registry"})
        self.env.start(); self.addCleanup(self.env.stop)

    def call(self, boundary, question, provider, previous=None, progress=None):
        payload = dict(question=question, previous_question=previous, **SCOPE)
        if boundary == "facade":
            result = SemanticAssistant(self.engine, provider).chat(AssistantTurnRequest.model_validate(payload), progress=progress)
        else:
            from fastapi.testclient import TestClient
            from academic_assistant import api
            with patch.object(api, "_engine", return_value=self.engine), patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=provider):
                response = TestClient(api.app).post("/v1/academic/assistant", json=payload)
            self.assertEqual(200, response.status_code)
            self.assertEqual("no-store", response.headers["cache-control"])
            self.assertLess(len(response.content), 512000)
            result = AssistantTurnResponse.model_validate(response.json())
        self.assertEqual(provider.entered, provider.exited)
        self.assertLessEqual(len(provider.plans), 1)
        self.assertLessEqual(len(provider.writes), 2)
        return result

    def assert_sources(self, response):
        seen_courses, seen_rules = set(), set()
        for part in response.parts:
            if part.course_evidence:
                packet = part.course_evidence
                self.assertEqual(SCOPE, packet.scope.model_dump())
                for fact, cite in zip(packet.courses, packet.evidence, strict=True):
                    expected = self.expected["course_facts"][fact.course_code]
                    self.assertEqual(expected, fact.model_dump())
                    self.assertEqual((expected["source_id"], expected["source_sha256"], expected["locator"], expected["course_id"]),
                                     (cite.source_id, cite.source_sha256, cite.locator, cite.course_id))
                    seen_courses.add(fact.course_code)
            if part.evidence_packet:
                packet = part.evidence_packet
                for applied in packet.applied_rules:
                    seen_rules.add(applied.rule_id)
                    self.assertEqual(self.expected["rule_fact_sha256"][applied.rule_id], applied.rule_sha256)
                    for cite in self.expected["rule_facts"][applied.rule_id]["evidence"]:
                        self.assertIn((cite["source_id"], cite["locator"]), {(c.source_id, c.locator) for c in packet.evidence})
        return seen_courses, seen_rules

    def campaign_plan(self, expected):
        cid = expected["case_id"]
        if expected["expected_kind"] == "greeting":
            return planned(dict(kind="greeting"))
        if expected["expected_kind"] == "scope_refusal":
            return planned(dict(kind="out_of_scope"))
        if expected.get("genuine_evidence_gap"):
            name = self.expected["course_facts"][expected["resolved_course_ids"][0]]["course_name"]
            return planned(courses(name, (), "description"))
        requests = []
        if "requirements_overview" in expected["expected_kind"]:
            requests.append(dict(kind="requirements_overview"))
        elif cid == "repair-09":
            requests.append(dict(kind="rule", intent_ids=["general.balanced-area-coverage"]))
        elif cid == "repair-12":
            requests.append(dict(kind="rule", intent_ids=["major.counseling-completion"]))
        elif cid == "repair-13":
            requests.append(dict(kind="rule", intent_ids=["operations.graduation-work-prerequisite"]))
        for code, properties in expected.get("required_course_properties", {}).items():
            purpose = "completion_obligation" if "completion_obligation" in expected["expected_kind"] else "attributes"
            requests.append(courses(self.expected["course_facts"][code]["course_name"], properties, purpose))
        for query in expected.get("course_queries", []):
            filters = deepcopy(query["filters"])
            if "semester" in filters:
                filters["semester"] = int(filters["semester"])
            requests.append(courses(properties=query["properties"], **filters))
        return planned(*requests, context_used=expected["context"]["used"])

    def campaign(self, case_id):
        expected = self.expected["cases"][case_id]
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary, case_id=case_id):
                provider = TypedProvider(self.campaign_plan(expected))
                result = self.call(boundary, expected["question"], provider, expected["previous_question"])
                self.assertEqual(expected["expected_status"], result.status)
                self.assertEqual(expected["context"]["used"], result.context_used)
                courses_seen, rules_seen = self.assert_sources(result)
                self.assertTrue(set(expected["required_course_ids"]) <= courses_seen)
                missing_rules = set(expected["required_rule_ids"]) - rules_seen
                for alternative in expected.get("approved_authority_alternatives", []):
                    if not set(alternative["required_rule_ids"]) <= rules_seen:
                        continue
                    item = alternative["required_item"]
                    source_items = self.expected["rule_facts"][item["rule_id"]]["decision"]["outcome"]["items"]
                    self.assertIn({"item_id": item["course_code"], "label": item["course_name"]}, source_items)
                    completion = alternative["completion_outcome"]
                    source_outcome = self.expected["rule_facts"][completion["rule_id"]]["decision"]["outcome"]
                    for key in ("required", "result_if_incomplete", "credit_value"):
                        self.assertEqual(completion[key], source_outcome[key])
                    self.assertIn(item["course_code"], courses_seen)
                    self.assertEqual(0, self.expected["course_facts"][item["course_code"]]["credits"])
                    missing_rules.discard(alternative["satisfies_rule_id"])
                self.assertEqual(set(), missing_rules)
                if expected["expected_kind"] in {"greeting", "scope_refusal"}:
                    self.assertEqual(expected["expected_wire_kind"], result.kind)
                    if expected["expected_kind"] == "greeting":
                        self.assertEqual([], result.parts)
                    else:
                        for part in result.parts:
                            self.assertEqual("out_of_scope", part.status)
                            self.assertIsNone(part.evidence_packet)
                            self.assertIsNone(part.course_evidence)
                            self.assertNotRegex(part.text, r"\d+\s*학점|CDA\d+")
                    continue
                if expected.get("genuine_evidence_gap"):
                    self.assertEqual("no_matching_evidence", result.reason_code)
                    continue
                self.assertEqual(("generated", "generated", None), (result.plan_status, result.generation_status, result.reason_code))
                for code, props in expected["required_course_properties"].items():
                    source = self.expected["course_facts"][code]
                    bound = " ".join(p.text for p in result.parts if p.course_evidence and any(f.course_code == code for f in p.course_evidence.courses))
                    for prop in props:
                        if prop == "credits":
                            self.assertRegex(bound, re.escape(source["course_name"]) + rf"[^.!?\n]{{0,80}}{source['credits']}\s*학점")
                        elif prop == "category":
                            self.assertIn(LABELS[source["category"]], bound)
                        elif prop == "offering":
                            self.assertIn(source["offering_label"], bound)
                for query in expected.get("course_queries", []):
                    sets = [{f.course_code for f in p.course_evidence.courses} for p in result.parts if p.course_evidence]
                    self.assertIn(set(query["course_codes"]), sets)
                    if "count" in query["properties"]:
                        self.assertRegex(result.answer, LABELS[query["filters"]["category"]] + rf"[^.!?\n]{{0,60}}{query['count']}\s*(?:개|과목)")
                    if "names" in query["properties"]:
                        for code in query["course_codes"]:
                            self.assertIn(self.expected["course_facts"][code]["course_name"], result.answer)

    def test_typed_independent_coverage_prevents_missing_facets(self):
        candidates = [("컴퓨터구조 내용과 학점을 알려줘", courses("컴퓨터구조"), courses("컴퓨터구조", ("credits",), "description")),
                      ("컴구 학점과 이수구분 알려줘", courses("컴퓨터구조"), courses("컴퓨터구조", ("credits", "category"))),
                      ("전체 졸업요건과 컴구 학점을 알려줘", dict(kind="requirements_overview"), courses("컴퓨터구조"))]
        for question, request, missing in candidates:
            for boundary in ("facade", "api"):
                with self.subTest(question=question, boundary=boundary):
                    model = TypedProvider(planned(request, coverage=[request, missing]))
                    result = self.call(boundary, question, model)
                    self.assertNotEqual("supported", result.status)
                    self.assertEqual("processing_unavailable", result.reason_code)
                    self.assertNotEqual("no_matching_evidence", result.reason_code)

    def test_missing_typed_coverage_is_processing_failure(self):
        document = planned(courses("컴퓨터구조")); del document["coverage"]
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary):
                model = TypedProvider(document)
                result = self.call(boundary, "컴구 학점", model)
                self.assertEqual(("rejected", "processing_unavailable"), (result.plan_status, result.reason_code))
                self.assertEqual([], model.writes)

    def test_wrong_progress_academic_claim_is_never_emitted(self):
        from academic_assistant.progress import ProgressReporter
        events = []
        model = TypedProvider(planned(courses("컴퓨터구조"), explanation="컴퓨터구조는 9학점입니다."))
        result = self.call("facade", "컴구 학점", model, progress=ProgressReporter(events.append))
        self.assertEqual(3, result.parts[0].course_evidence.courses[0].credits)
        self.assertNotIn("9학점", json.dumps(events, ensure_ascii=False))
        self.assertNotIn("9학점", result.answer)
        self.assertTrue(events)
        self.assertEqual(list(range(1, len(events) + 1)), [e["sequence"] for e in events])
        self.assertEqual(sorted(e["elapsed_ms"] for e in events), [e["elapsed_ms"] for e in events])

    def test_word_numeral_and_speculative_academic_progress_not_action_prose(self):
        from academic_assistant.progress import ProgressReporter
        unsafe = ("컴퓨터구조는 아홉 학점으로 알고 있으며 질문을 확인합니다.",
                  "컴퓨터구조는 네 학점으로 알고 있으며 질문을 확인합니다.",
                  "컴구는 세 학점일 것으로 생각하며 자료를 찾습니다.")
        safe = "컴퓨터구조의 학점 정보를 찾도록 질문을 정리하고 확인합니다."
        for explanation in (*unsafe, safe):
            with self.subTest(explanation=explanation):
                events = []
                model = TypedProvider(planned(courses("컴퓨터구조"), explanation=explanation))
                result = self.call("facade", "컴구 학점", model, progress=ProgressReporter(events.append))
                self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                self.assert_sources(result)
                self.assertEqual(3, result.parts[0].course_evidence.courses[0].credits)
                emitted = [e["message"] for e in events if e.get("explanation_source") == "llm"]
                if explanation in unsafe:
                    self.assertNotIn(explanation, emitted)
                    self.assertNotIn(explanation, result.answer)
                else:
                    # Optional omission is not an academic wrong answer. If
                    # emitted, this must be the safe operation description;
                    # otherwise actual system intent progress remains visible.
                    if emitted:
                        self.assertIn(explanation, emitted)
                    else:
                        self.assertTrue(any(e.get("stage") == "intent" and e.get("explanation_source") == "system" for e in events))
                self.assertEqual(list(range(1, len(events) + 1)), [e["sequence"] for e in events])
                self.assertEqual(sorted(e["elapsed_ms"] for e in events), [e["elapsed_ms"] for e in events])

    def test_unknown_short_department_student_context_is_not_cs_conjunction(self):
        external = ("통계과 학생인데 졸업요건을 알려줘", "가람과 학생입니다. 졸업요건을 알려줘",
                    "통계과에 다니는 학생의 졸업요건", "전자과 학생의 졸업요건")
        for question in external:
            for boundary in ("facade", "api"):
                with self.subTest(question=question, boundary=boundary):
                    model = TypedProvider(planned(dict(kind="requirements_overview")))
                    result = self.call(boundary, question, model)
                    self.assertEqual(("out_of_scope", "unsupported_scope"), (result.status, result.reason_code))
                    self.assertEqual([], model.plans)
                    self.assertEqual([], model.writes)
                    self.assertTrue(all(p.evidence_packet is None and p.course_evidence is None for p in result.parts))
        for boundary in ("facade", "api"):
            model = TypedProvider(planned(dict(kind="requirements_overview")))
            result = self.call(boundary, "컴퓨터공학과 학생의 졸업요건", model)
            self.assertEqual("supported", result.status)
            self.assertEqual(1, len(model.plans))
            self.assert_sources(result)

    def test_accurate_extra_attributes_allowed_wrong_attributes_rejected(self):
        variants = [("컴퓨터구조는 3학점이고 전공필수입니다. 3학년 1학기 편성입니다.", True),
                    ("컴퓨터구조는 전공선택이 아니라 전공필수이고 3학점입니다.", True),
                    ("컴퓨터구조는 3학점이고 전공선택입니다.", False),
                    ("컴퓨터구조는 3학점입니다. 이 과목은 1학년 1학기입니다.", False)]
        for text, valid in variants:
            def writer(payload):
                result = prose(payload); result["sections"][0]["text"] = text; return result
            for boundary in ("facade", "api"):
                with self.subTest(valid=valid, text=text, boundary=boundary):
                    model = TypedProvider(planned(courses("컴퓨터구조")), writer)
                    result = self.call(boundary, "컴구 학점", model)
                    self.assert_sources(result)
                    if valid:
                        self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                        self.assertIn(text, result.answer)
                    else:
                        self.assertEqual(("fallback", "processing_unavailable"), (result.generation_status, result.reason_code))
                        self.assertNotIn(text, result.answer)

    def test_truthful_category_correction_direction_keeps_bound_course_attributes(self):
        variants = (("컴퓨터구조는 3학점이며 전공필수이고 전공선택은 아닙니다.", True),
                    ("컴퓨터구조는 전선이 아니라 전필이며 3학점입니다.", True),
                    ("컴퓨터구조는 전공필수가 아니라 전공선택이고 3학점입니다.", False),
                    ("컴퓨터구조는 전공필수이며 9학점입니다.", False))
        for text, valid in variants:
            def writer(payload):
                value = prose(payload); value["sections"][0]["text"] = text; return value
            for boundary in ("facade", "api"):
                with self.subTest(text=text, valid=valid, boundary=boundary):
                    model = TypedProvider(planned(courses("컴퓨터구조")), writer)
                    result = self.call(boundary, "컴구 학점", model)
                    self.assert_sources(result)
                    if valid:
                        self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                        self.assertIn(text, result.answer)
                    else:
                        self.assertEqual(("insufficient_evidence", "fallback", "processing_unavailable"),
                                         (result.status, result.generation_status, result.reason_code))
                        self.assertNotIn(text, result.answer)

    def test_catalogue_count_cannot_license_wrong_extra_course_category(self):
        for text, valid in (("조회한 목록은 43과목입니다. 컴퓨터구조는 전공필수입니다.", True),
                            ("조회한 목록은 43과목입니다. 컴퓨터구조는 전공선택입니다.", False)):
            def writer(payload):
                value = prose(payload); value["sections"][0]["text"] = text; return value
            for boundary in ("facade", "api"):
                with self.subTest(valid=valid, boundary=boundary):
                    model = TypedProvider(planned(courses(properties=("count",))), writer)
                    result = self.call(boundary, "전체 과목 수", model)
                    self.assertEqual(43, len(result.parts[0].course_evidence.courses))
                    self.assert_sources(result)
                    self.assertEqual("generated" if valid else "fallback", result.generation_status)
                    if not valid:
                        self.assertNotIn(text, result.answer)

    def test_prerequisite_direction_and_negation_are_rejected(self):
        variants = ["졸업작품을 PASS해야 캡스톤디자인 II를 수강할 수 있습니다. 졸업 가능 여부를 판정하지 않습니다. 학점 취득을 판정하지 않습니다.",
                    "졸업작품을 수강하려면 캡스톤디자인 II를 PASS하지 않아도 됩니다. 졸업 가능 여부를 판정하지 않습니다. 학점 취득을 판정하지 않습니다."]
        for text in variants:
            def writer(payload):
                result = prose(payload); result["sections"][0]["text"] = text; return result
            for boundary in ("facade", "api"):
                with self.subTest(boundary=boundary, mutation=text):
                    model = TypedProvider(planned(dict(kind="rule", intent_ids=["operations.graduation-work-prerequisite"])), writer)
                    result = self.call(boundary, "졸업작품 수강의 선행조건을 알려줘", model)
                    self.assertEqual(("fallback", "processing_unavailable"), (result.generation_status, result.reason_code))
                    self.assertNotIn(text, result.answer)
                    self.assert_sources(result)

    def test_source_correct_topic_and_relative_clause_prerequisite_are_generated(self):
        caveat = " 졸업 가능 여부를 판정하지 않습니다. 학점 취득을 판정하지 않습니다."
        variants = ("졸업작품은 캡스톤디자인 II를 PASS한 학생만 수강할 수 있습니다.",
                    "졸업작품은 캡스톤디자인 II를 통과해야 수강할 수 있습니다.",
                    "캡스톤디자인 II를 PASS한 학생만 졸업작품을 수강할 수 있습니다.")
        rule_id = "cwnu.cs.2026.operations.graduation-work-prerequisite"
        outcome = self.expected["rule_facts"][rule_id]["decision"]["outcome"]
        self.assertEqual(("capstone.ii.pass", "graduation.work.enrollment", False, False),
                         (outcome["prerequisite"], outcome["requirement"], outcome["sufficient_for_graduation"], outcome["automatic_credits"]))
        for text in variants:
            def writer(payload):
                value = prose(payload); value["sections"][0]["text"] = text + caveat; return value
            for boundary in ("facade", "api"):
                with self.subTest(text=text, boundary=boundary):
                    model = TypedProvider(planned(dict(kind="rule", intent_ids=["operations.graduation-work-prerequisite"])), writer)
                    result = self.call(boundary, "졸업작품의 수강 선행조건을 알려줘", model)
                    self.assertEqual(("supported", "generated", None), (result.status, result.generation_status, result.reason_code))
                    self.assertEqual(1, len(model.writes))
                    self.assertIn(text, result.answer)
                    self.assertIn(caveat.strip(), result.answer)
                    self.assertIn(rule_id, self.assert_sources(result)[1])

    def test_general_noun_conjunction_not_a_department(self):
        for token in ("이름과", "의견과", "구성과"):
            for boundary in ("facade", "api"):
                with self.subTest(token=token, boundary=boundary):
                    model = TypedProvider(planned(courses("컴퓨터구조", ("names", "credits"))))
                    result = self.call(boundary, "컴퓨터구조의 " + token + " 학점을 확인해줘", model)
                    self.assertNotEqual("out_of_scope", result.status)
                    self.assertEqual(1, len(model.plans))
                    self.assert_sources(result)
        model = TypedProvider(planned(dict(kind="requirements_overview")))
        refused = self.call("api", "전자공학과 졸업요건", model)
        self.assertEqual("out_of_scope", refused.status)
        self.assertEqual([], model.plans)

    def test_transient_contact_and_names_allowed_legacy_feedback_protected(self):
        from fastapi.testclient import TestClient
        from academic_assistant import api
        for question in ("홍길동 학번 2026000001 컴구 학점", "qa@example.invalid 컴구 학점", "010-1234-5678 연락처를 적었고 컴구 학점이 궁금해"):
            for boundary in ("facade", "api"):
                with self.subTest(question=question, boundary=boundary):
                    model = TypedProvider(planned(courses("컴퓨터구조")))
                    result = self.call(boundary, question, model)
                    self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                    self.assertEqual(question, model.plans[0]["question"])
                    self.assert_sources(result)
        with patch.object(api, "_engine", return_value=self.engine):
            response = TestClient(api.app).post("/v1/academic/answers", json=dict(question="홍길동 학번 2026000001 졸업학점", **SCOPE))
        self.assertEqual(422, response.status_code)
        self.assertNotIn("홍길동", response.text)

    def test_retry_uses_actual_failure_same_facts_and_no_canned_success(self):
        calls = []
        def writer(payload):
            calls.append(deepcopy(payload))
            result = prose(payload)
            if len(calls) == 1:
                result["sections"][0]["text"] = "컴퓨터구조는 9학점입니다."
            return result
        model = TypedProvider(planned(courses("컴퓨터구조")), writer)
        result = self.call("api", "컴구 학점", model)
        self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
        self.assertEqual(2, len(calls))
        self.assertEqual(calls[0]["parts"], calls[1]["parts"])
        self.assertNotEqual(set(calls[0]), set(calls[1]), "Retry must carry observed verification failure, not silently repeat.")
        self.assertNotIn("9학점", result.answer)
        def wrong(payload):
            document = prose(payload); document["sections"][0]["text"] = "컴퓨터구조는 9학점입니다."; return document
        failed = TypedProvider(planned(courses("컴퓨터구조")), wrong)
        response = self.call("api", "컴구 학점", failed)
        self.assertEqual(2, len(failed.writes))
        self.assertEqual(("fallback", "processing_unavailable"), (response.generation_status, response.reason_code))
        self.assertNotIn("9학점", response.answer)
        self.assertNotEqual("supported", response.status)
        self.assert_sources(response)

    def test_known_attribute_heads_remain_bound_and_unknown_course_claims_fail(self):
        # Source-derived values, not a production verifier as the oracle.
        structure = self.expected["course_facts"]["CDA0016"]
        database = self.expected["course_facts"]["CDA0065"]
        cases = (("컴퓨터구조", ("credits",), f"컴퓨터구조의 학점 정보는 {structure['credits']}학점입니다.", True),
                 ("데이타베이스개론", ("offering",), f"데이타베이스개론의 교육과정 편성 정보는 {database['offering_label']}입니다.", True),
                 ("컴퓨터구조", ("credits",), "미등록교과목의 학점 정보는 3학점입니다.", False),
                 ("데이타베이스개론", ("offering",), "미등록교과목의 교육과정 편성 정보는 3학년 1학기입니다.", False))
        self.assertEqual((3, "major_required"), (structure["credits"], structure["category"]))
        self.assertEqual("3학년 1학기", database["offering_label"])
        for name, props, text, valid in cases:
            def writer(payload):
                document = prose(payload); document["sections"][0]["text"] = text; return document
            for boundary in ("facade", "api"):
                with self.subTest(name=name, text=text, boundary=boundary):
                    model = TypedProvider(planned(courses(name, props)), writer)
                    result = self.call(boundary, name + "의 " + ("학점" if "credits" in props else "교육과정 편성") + "을 알려줘", model)
                    self.assert_sources(result)
                    if valid:
                        self.assertEqual(("supported", "generated", None), (result.status, result.generation_status, result.reason_code))
                        self.assertIn(text, result.answer)
                    else:
                        self.assertEqual(("insufficient_evidence", "fallback", "processing_unavailable"), (result.status, result.generation_status, result.reason_code))
                        self.assertNotIn(text, result.answer)

    def test_department_roles_not_course_conjunctions_or_name_labels(self):
        external = ("통계과 소속 학생의 졸업요건 알려줘", "해양과에 속한 학생인데 졸업요건 알려줘",
                    "가람과 재학 중인 학생 졸업요건 알려줘", "기계과의 다른 전공학점")
        for text in external:
            for boundary in ("facade", "api"):
                with self.subTest(text=text, boundary=boundary):
                    model = TypedProvider(planned(dict(kind="requirements_overview")))
                    result = self.call(boundary, text, model)
                    self.assertEqual(("out_of_scope", "unsupported_scope"), (result.status, result.reason_code))
                    self.assertEqual(([], []), (model.plans, model.writes))
                    self.assertTrue(all(p.evidence_packet is None and p.course_evidence is None for p in result.parts))
        for boundary in ("facade", "api"):
            model = TypedProvider(planned(courses("소프트웨어공학"), courses("컴퓨터구조")))
            result = self.call(boundary, "소프트웨어공학과 컴퓨터구조 학점을 각각 알려줘", model)
            self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
            self.assertEqual({"CDA0028", "CDA0016"}, self.assert_sources(result)[0])
            self.assertEqual(1, len(model.plans))
            names = TypedProvider(planned(courses(properties=("names",), category="major_required")))
            named = self.call(boundary, "과목 이름과 학생 이름을 알려줘", names)
            self.assertEqual(("supported", "generated"), (named.status, named.generation_status))
            self.assertEqual(9, len(self.assert_sources(named)[0]))
            self.assertEqual("과목 이름과 학생 이름을 알려줘", names.plans[0]["question"])

    def test_required_pass_case_condition_not_whether_or_not(self):
        rule_id = "cwnu.cs.2026.operations.graduation-work-prerequisite"
        outcome = self.expected["rule_facts"][rule_id]["decision"]["outcome"]
        self.assertEqual(("capstone.ii.pass", "graduation.work.enrollment", False, False),
                         (outcome["prerequisite"], outcome["requirement"], outcome["sufficient_for_graduation"], outcome["automatic_credits"]))
        caveat = " 졸업 가능 여부를 판정하지 않습니다. 학점 취득을 판정하지 않습니다."
        cases = (("졸업작품은 캡스톤디자인 II를 PASS한 경우에만 수강할 수 있습니다.", True),
                 ("캡스톤디자인 II를 PASS한 경우에만 졸업작품 수강 신청이 가능합니다.", True),
                 ("졸업작품의 수강 선행조건은 캡스톤디자인 II를 PASS해도 안해도 충족됩니다.", False),
                 ("졸업작품은 캡스톤디자인 II를 PASS하든 하지 않든 수강할 수 있습니다.", False))
        for text, valid in cases:
            def writer(payload):
                document = prose(payload); document["sections"][0]["text"] = text + caveat; return document
            for boundary in ("facade", "api"):
                with self.subTest(text=text, boundary=boundary):
                    model = TypedProvider(planned(dict(kind="rule", intent_ids=["operations.graduation-work-prerequisite"])), writer)
                    result = self.call(boundary, "졸업작품의 수강 선행조건을 알려줘", model)
                    self.assertIn(rule_id, self.assert_sources(result)[1])
                    if valid:
                        self.assertEqual(("supported", "generated", None), (result.status, result.generation_status, result.reason_code))
                        self.assertIn(text, result.answer)
                        self.assertIn(caveat.strip(), result.answer)
                    else:
                        self.assertEqual(("insufficient_evidence", "fallback", "processing_unavailable"), (result.status, result.generation_status, result.reason_code))
                        self.assertNotIn(text, result.answer)
        # The original case explicitly mentions the prerequisite participant.
        # Its approved RuleFact suffices for this relationship, not extra
        # unrequested course credits/category. Original campaign remains strict.
        self.campaign("repair-13")

    def test_opposing_category_negation_not_wrong_actual_category(self):
        fact = self.expected["course_facts"]["CDA0016"]
        self.assertEqual((3, "major_required"), (fact["credits"], fact["category"]))
        cases = (("컴퓨터구조는 전공선택으로 분류되지 않았고 전공필수이며 3학점입니다.", True),
                 ("컴퓨터구조는 전공필수이며 전공선택 과목이 아니고 3학점입니다.", True),
                 ("컴퓨터구조는 전공필수로 분류되지 않았고 전공선택이며 3학점입니다.", False),
                 ("컴퓨터구조는 전공필수 과목이 아니고 전공선택이며 3학점입니다.", False))
        for text, valid in cases:
            def writer(payload):
                document = prose(payload); document["sections"][0]["text"] = text; return document
            for boundary in ("facade", "api"):
                with self.subTest(text=text, boundary=boundary):
                    model = TypedProvider(planned(courses("컴퓨터구조", ("credits", "category"))), writer)
                    result = self.call(boundary, "컴구 학점과 이수구분", model)
                    self.assertEqual({"CDA0016"}, self.assert_sources(result)[0])
                    if valid:
                        self.assertEqual(("supported", "generated", None), (result.status, result.generation_status, result.reason_code))
                        self.assertIn(text, result.answer)
                    else:
                        self.assertEqual(("insufficient_evidence", "fallback", "processing_unavailable"), (result.status, result.generation_status, result.reason_code))
                        self.assertNotIn(text, result.answer)

    def test_repair_operation_explanation_does_not_change_immutable_evidence(self):
        from academic_assistant.progress import ProgressReporter
        safe = "조회한 정보와 작성한 문장 수치를 비교하고 답변 표현을 교정합니다."
        unsafe = ("조회 근거의 숫자를 수정하고 오류를 교정합니다.", "근거의 오류를 수정하고 답변을 작성합니다.",
                  "서버 오류 때문에 답이 틀렸으므로 문장을 교정합니다.")
        for explanation in (None, safe, *unsafe):
            with self.subTest(explanation=explanation):
                calls = []
                def writer(payload):
                    calls.append(deepcopy(payload))
                    document = prose(payload)
                    if len(calls) == 1:
                        document["sections"][0]["text"] = "컴퓨터구조는 9학점입니다."
                    elif explanation is not None:
                        document["explanation"] = explanation
                    return document
                events = []
                model = TypedProvider(planned(courses("컴퓨터구조"), explanations=dict(repair="질문을 확인하고 문장을 교정합니다.")), writer)
                result = self.call("facade", "컴구 학점", model, progress=ProgressReporter(events.append))
                self.assertEqual(("supported", "generated", None), (result.status, result.generation_status, result.reason_code))
                self.assert_sources(result)
                self.assertEqual(2, len(calls))
                self.assertEqual(calls[0]["parts"], calls[1]["parts"])
                self.assertEqual("unsupported_number", calls[1]["correction"]["error_code"])
                self.assertEqual("컴퓨터구조는 9학점입니다.", calls[1]["correction"]["previous_document"]["sections"][0]["text"])
                emitted = [e for e in events if e.get("stage") == "repair" and e.get("explanation_source") == "llm"]
                if explanation in unsafe or explanation is None:
                    self.assertEqual([], emitted)
                elif emitted:
                    self.assertTrue(all(e["message"] == safe and e.get("details", {}).get("error_code") == "unsupported_number" for e in emitted))
                self.assertFalse(any(e.get("message") == "질문을 확인하고 문장을 교정합니다." for e in events))
                self.assertNotIn("9학점", result.answer)
                self.assertIn("3학점", result.answer)

    def test_course_named_operation_progress_not_academic_predicate(self):
        from academic_assistant.progress import ProgressReporter
        safe = ("컴퓨터구조의 학점 정보를 찾도록 질문을 정리하고 확인합니다.", "컴퓨터구조 과목의 공개 정보를 조회하고 확인합니다.")
        unsafe = ("컴퓨터구조는 아홉 학점으로 알고 있으며 질문을 확인합니다.", "컴구는 세 학점일 것으로 생각하며 자료를 찾습니다.",
                  "컴퓨터구조는 전공선택으로 판정되었고 질문을 확인합니다.")
        for explanation in (*safe, *unsafe):
            with self.subTest(explanation=explanation):
                events = []
                model = TypedProvider(planned(courses("컴퓨터구조"), explanation=explanation))
                result = self.call("facade", "컴구 학점", model, progress=ProgressReporter(events.append))
                self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                self.assertEqual({"CDA0016"}, self.assert_sources(result)[0])
                emitted = [e["message"] for e in events if e.get("explanation_source") == "llm"]
                if explanation in unsafe:
                    self.assertNotIn(explanation, emitted)
                    self.assertNotIn(explanation, result.answer)
                elif emitted:
                    self.assertTrue(all(message in safe for message in emitted))
                self.assertTrue(any(e.get("stage") == "intent" for e in events))


class BDialogueTransportQA(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, application, *, accept, stall=False):
        body = json.dumps(dict(question="안녕", **SCOPE)).encode()
        messages, received, blocked = [], False, False
        never = asyncio.Event()
        async def receive():
            nonlocal received
            if not received:
                received = True
                return dict(type="http.request", body=body, more_body=False)
            await never.wait()
        async def send(event):
            nonlocal blocked
            if stall and event["type"] == "http.response.body" and event.get("body") and not blocked:
                blocked = True
                await asyncio.sleep(1.3)
            messages.append(deepcopy(event))
        scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method="POST",
                     scheme="http", path="/v1/academic/assistant", raw_path=b"/v1/academic/assistant", query_string=b"",
                     root_path="", headers=[(b"host", b"localhost"), (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"accept", accept.encode())],
                     client=("127.0.0.1", 19876), server=("localhost", 80))
        await asyncio.wait_for(application(scope, receive, send), timeout=6)
        return messages

    @staticmethod
    def greeting():
        return AssistantTurnResponse(packet_id="qa-transport", status="out_of_scope", kind="greeting", answer="안녕하세요.", plan_status="generated", generation_status="not_applicable", parts=[])

    async def test_actual_api_queue_saturation_terminal_and_public_capacity(self):
        from academic_assistant import api
        from academic_assistant.public_demo import PublicDemo, DemoLimits
        engine = AnswerEngine(Registry.load(ROOT))
        done = threading.Event()
        greeting = self.greeting()
        class SaturatingAssistant:
            def __init__(self, **kwargs):
                pass
            def chat(self, request, progress=None):
                try:
                    for index in range(150):
                        if progress:
                            progress.emit("query", "info", "합성 전송 경계 관찰", details={"ordinal": index})
                    return greeting
                finally:
                    done.set()
        wrapper = PublicDemo(api.app, DemoLimits(work_seconds=6, posts_per_minute=20))
        with patch.object(api, "_engine", return_value=engine), patch("academic_assistant.assistant.SemanticAssistant", SaturatingAssistant):
            events = await self.invoke(wrapper, accept="application/x-ndjson", stall=True)
            self.assertEqual(200, next(e["status"] for e in events if e["type"] == "http.response.start"))
            self.assertTrue(done.is_set(), "Actual synchronous producer must have exited.")
            self.assertEqual((0, 0), (wrapper.active, wrapper.work_active))
            raw = b"".join(e.get("body", b"") for e in events if e["type"] == "http.response.body")
            rows = [json.loads(line) for line in raw.splitlines()]
            self.assertTrue(rows)
            self.assertIn(rows[-1]["type"], {"error", "result"})
            self.assertEqual(1, sum(r["type"] in {"error", "result"} for r in rows))
            self.assertFalse(events[-1].get("more_body", False))
            second = await self.invoke(wrapper, accept="application/json")
            self.assertEqual(200, next(e["status"] for e in second if e["type"] == "http.response.start"))
            self.assertEqual((0, 0), (wrapper.active, wrapper.work_active))

    async def test_exact_quality_aware_accept_matches_api_and_public_wrapper(self):
        from academic_assistant import api
        from academic_assistant.public_demo import PublicDemo
        engine = AnswerEngine(Registry.load(ROOT))
        response = self.greeting()
        class FastAssistant:
            def __init__(self, **kwargs):
                pass
            def chat(self, request, progress=None):
                if progress:
                    progress.emit("received", "completed", "합성 요청 확인")
                return response
        variants = [("application/x-ndjson-ish", False), ("application/x-ndjson;q=0", False),
                    ("application/json, application/x-ndjson;q=0.5", True), ("application/x-ndjson", True)]
        with patch.object(api, "_engine", return_value=engine), patch("academic_assistant.assistant.SemanticAssistant", FastAssistant):
            for accept, streaming in variants:
                for app in (api.app, PublicDemo(api.app)):
                    with self.subTest(accept=accept, wrapper=isinstance(app, PublicDemo)):
                        events = await self.invoke(app, accept=accept)
                        self.assertEqual(200, next(e["status"] for e in events if e["type"] == "http.response.start"))
                        headers = dict(next(e["headers"] for e in events if e["type"] == "http.response.start"))
                        self.assertEqual(streaming, b"application/x-ndjson" in headers.get(b"content-type", b""))
                        raw = b"".join(e.get("body", b"") for e in events if e["type"] == "http.response.body")
                        if streaming:
                            self.assertEqual("result", json.loads(raw.splitlines()[-1])["type"])
                        else:
                            self.assertEqual("greeting", json.loads(raw)["kind"])


def _campaign_test(case_id):
    def test(self):
        self.campaign(case_id)
    return test


for _id in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]:
    setattr(BDialogueSourceQA, "test_source_" + _id.replace("-", "_"), _campaign_test(_id))


if __name__ == "__main__":
    unittest.main()
