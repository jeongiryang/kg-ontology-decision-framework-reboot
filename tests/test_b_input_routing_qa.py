"""Independent B input-routing QA at real facade and ASGI API boundaries.

Only the semantic provider is scripted. No external model, service, source
approval or product helper supplies the expected academic answer. The synthetic
campaign was made public only after the final cycle2 producer freeze.
These checks do not establish live-model understanding or Neo4j availability.
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

from academic_assistant.assistant import SemanticAssistant
from academic_assistant.assistant_models import AssistantTurnRequest, AssistantTurnResponse
from academic_assistant.core import AnswerEngine
from academic_assistant.registry import Registry
from academic_assistant.semantic_llm import SemanticDocument

EXPECTATIONS_PATH = ROOT / "tests/fixtures/b-input-routing-20261002.json"
QUESTIONS_PATH = EXPECTATIONS_PATH
SCOPE = dict(admission_year=2026, matched_curriculum_year=2026, department="컴퓨터공학과")
LABELS = {"major_required": "전공필수", "major_elective": "전공선택"}
SOURCE_PINS = {
    "cwnu.curriculum.2026.changwon-undergraduate": "0d800318dc367a89518f9be6be8f559092474bf00b3573e14bd37b6c44b54471",
    "cwnu.curriculum.2026.ta-validation-response": "c5a7839675bd02679018788190961613d68b3126ba411b060710987162cb336d",
    "cwnu.cs.2026.department-confirmation-20261001": "d859f17bd70b39f90c5fa5b906c960e0b79debbf3dcdaa051bee17aaf3674ac6",
}
OBSERVATIONS = []


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def course(name, properties=("credits",), purpose="attributes", **filters):
    return dict(kind="courses", filters={**({"name": name} if name else {}), **filters}, purpose=purpose, properties=list(properties))


def plan(*requests, context_used=False):
    return dict(requests=list(requests), context_used=context_used)


def projected_prose(payload):
    """Untrusted writer input, not an expected-answer oracle or product template."""
    sections = []
    for part in payload["parts"]:
        props = set(part.get("properties", []))
        sentences = []
        summary = part.get("course_summary")
        if summary is not None and props & {"count", "names"}:
            category = summary["filters"].get("category")
            label = LABELS.get(category, "조회한")
            sentences.append(f"{label} 과목은 {summary['course_count']}개입니다.")
        for fact in part["facts"]:
            if "course_name" not in fact:
                sentences.append(fact["statement"])
                items = fact.get("outcome", {}).get("items", [])
                if items:
                    sentences.append("지정 항목: " + ", ".join(item["label"] for item in items) + ".")
                continue
            if summary is not None and props <= {"count", "names"}:
                continue
            name = fact["course_name"]
            if "credits" in props:
                sentences.append(f"{name}는 {fact['credits']}학점입니다.")
            if "category" in props:
                sentences.append(f"{name}는 {LABELS[fact['category']]}입니다.")
            if "offering" in props:
                sentences.append(f"{name}는 {fact['offering_label']} 편성입니다.")
            if "code" in props:
                sentences.append(f"{name}의 과목코드는 {fact['code']}입니다.")
        sections.append(dict(part_id=part["part_id"], text=" ".join(sentences), fact_ids=[fact["fact_id"] for fact in part["facts"]]))
    return dict(sections=sections)


class ControlledTypedProvider:
    # Both this trusted provider capability and the result's provenance are
    # deliberately present. Otherwise production bypass would not be tested.
    typed_plans = True

    def __init__(self, document, writer=projected_prose, *, typed_plan=True):
        self.document = deepcopy(document)
        self.writer, self.typed_plan = writer, typed_plan
        self.plan_calls, self.write_calls = [], []
        self.entered = self.exited = 0
        self.result_override = None

    @contextmanager
    def session(self):
        self.entered += 1
        try:
            yield self
        finally:
            self.exited += 1

    def plan(self, payload):
        self.plan_calls.append(deepcopy(payload))
        if self.result_override is not None:
            return self.result_override
        return SemanticDocument(deepcopy(self.document), typed_plan=self.typed_plan)

    def write(self, payload):
        self.write_calls.append(deepcopy(payload))
        return SemanticDocument(self.writer(deepcopy(payload)))


class BInputIndependentQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_courses = {fact["course_code"]: fact for fact in json.loads((ROOT / "knowledge/course-catalogue.json").read_text(encoding="utf-8"))["courses"]}
        cls.raw_rules = {fact["rule_id"]: fact for fact in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "knowledge/rules").glob("*.json"))}
        cls.raw_sources = {fact["source_id"]: fact for fact in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "knowledge/sources").glob("*.json"))}
        cls.registry = Registry.load(ROOT)
        cls.engine = AnswerEngine(cls.registry)
        cls.response_schema = json.loads((ROOT / "contracts/assistant-turn-response.schema.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"ACADEMIC_LLM_PROVIDER": "disabled", "ACADEMIC_EVIDENCE_BACKEND": "registry"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def assert_source_identity(self, source_id):
        raw = self.raw_sources[source_id]
        self.assertEqual(SOURCE_PINS[source_id], raw["sha256"])
        self.assertEqual(("human", "full", "approved"), tuple(raw["review"][k] for k in ("mode", "scope", "status")))

    def call(self, boundary, question, model, *, previous_question=None, **updates):
        payload = dict(question=question, previous_question=previous_question, earned_credits={}, **SCOPE)
        payload.update(updates)
        http_status = None
        if boundary == "facade":
            result = SemanticAssistant(self.engine, model).chat(AssistantTurnRequest.model_validate(payload))
            data = result.model_dump()
        else:
            from fastapi.testclient import TestClient
            from academic_assistant import api
            with patch.object(api, "_engine", return_value=self.engine), patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=model):
                response = TestClient(api.app).post("/v1/academic/assistant", json=payload)
            http_status = response.status_code
            data = response.json()
            self.assertEqual(200, http_status)
            self.assertLess(len(response.content), 512000)
            self.assertEqual("no-store", response.headers["cache-control"])
            result = AssistantTurnResponse.model_validate(data)
        OBSERVATIONS.append(dict(test_id=self.id(), execution_kind="mock_typed_provider_real_" + boundary, request=payload, http_status=http_status, response=data,
                                 provider_plan_calls=deepcopy(model.plan_calls), provider_write_calls=deepcopy(model.write_calls)))
        self.assertEqual(model.entered, model.exited)
        self.assertLessEqual(len(model.plan_calls), 1)
        self.assertLessEqual(len(model.write_calls), 1)
        self.assertLessEqual(len(result.parts), 8)
        self.assertNotRegex(result.model_dump_json(), r"(?i)(?:[A-Z]:[\\/]|qa-private@example\.invalid|QAPRIVATE0001)")
        from jsonschema import Draft202012Validator
        Draft202012Validator(self.response_schema).validate(data)
        if model.plan_calls:
            self.assertEqual(question, model.plan_calls[0]["question"])
            self.assertEqual(previous_question, model.plan_calls[0]["previous_question"])
        return result

    def assert_courses(self, result, codes):
        packets = [part.course_evidence for part in result.parts if part.course_evidence is not None and part.status == "supported"]
        facts = [fact for packet in packets for fact in packet.courses]
        self.assertCountEqual(codes, [fact.course_code for fact in facts])
        for packet in packets:
            self.assertEqual("supported", packet.status)
            self.assertEqual(SCOPE, packet.scope.model_dump())
            self.assertEqual(len(packet.courses), len(packet.evidence))
            for fact, citation in zip(packet.courses, packet.evidence, strict=True):
                raw = self.raw_courses[fact.course_code]
                self.assertEqual(raw, fact.model_dump())
                self.assertEqual(raw["fact_sha256"], canonical_hash({k: v for k, v in raw.items() if k != "fact_sha256"}))
                self.assertEqual((raw["course_id"], raw["source_id"], raw["source_sha256"], raw["locator"]), (citation.course_id, citation.source_id, citation.source_sha256, citation.locator))
                self.assert_source_identity(citation.source_id)

    def assert_rules(self, result, expected_ids):
        observed = set()
        for part in result.parts:
            packet = part.evidence_packet
            if packet is None or part.status != "supported":
                continue
            self.assertIsNone(part.course_evidence)
            self.assertEqual("supported", packet.status)
            self.assertEqual(SCOPE, packet.scope.model_dump())
            for applied in packet.applied_rules:
                observed.add(applied.rule_id)
                raw = self.raw_rules[applied.rule_id]
                self.assertEqual(canonical_hash(raw), applied.rule_sha256)
                self.assertEqual(("human", "full", "approved"), tuple(raw["review"][k] for k in ("mode", "scope", "status")))
                self.assertEqual([2026], raw["applicability"]["admission_years"])
                self.assertEqual([2026], raw["applicability"]["curriculum_years"])
                self.assertEqual(["컴퓨터공학과"], raw["applicability"]["departments"])
                references = {(ref["source_id"], ref["locator"]) for ref in raw["evidence"]}
                citations = [ref for ref in packet.evidence if ref.rule_id == applied.rule_id]
                self.assertTrue(citations)
                for citation in citations:
                    self.assertIn((citation.source_id, citation.locator), references)
                    self.assertEqual(raw["decision"]["statement"], citation.claim)
                    # Rule citations intentionally carry no SHA field.
                    self.assert_source_identity(citation.source_id)
        self.assertTrue(set(expected_ids) <= observed, f"Missing approved rules: {set(expected_ids) - observed}")

    def assert_facets(self, result, code, properties):
        raw = self.raw_courses[code]
        parts = [part for part in result.parts if part.course_evidence and any(fact.course_code == code for fact in part.course_evidence.courses)]
        self.assertTrue(parts)
        body = " ".join(part.text for part in parts)
        self.assertIn(raw["course_name"], body)
        if "credits" in properties:
            self.assertRegex(body, re.escape(raw["course_name"]) + rf"[^.!?\n]{{0,60}}{raw['credits']}\s*학점")
        if "category" in properties:
            self.assertRegex(body, re.escape(raw["course_name"]) + r"[^.!?\n]{0,60}" + LABELS[raw["category"]])
        if "offering" in properties:
            self.assertIn(raw["offering_label"], body)
        if "code" in properties:
            self.assertIn(code, body)

    def campaign_case(self, case_id):
        oracle = json.loads(EXPECTATIONS_PATH.read_text(encoding="utf-8"))
        row = next(q for q in json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"] if q["case_id"] == case_id)
        expected = oracle["cases"][case_id]
        for code in expected["courses"]:
            self.assertEqual(self.raw_courses[code], oracle["course_facts"][code])
        for rule_id in expected["rules"]:
            self.assertEqual(self.raw_rules[rule_id], oracle["rule_facts"][rule_id])
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary):
                model = ControlledTypedProvider(plan(*expected["requests"], context_used=expected.get("context_used", False)))
                result = self.call(boundary, row["question"], model, previous_question=row.get("previous_question"))
                self.assertEqual(expected["status"], result.status)
                if expected.get("before_model"):
                    self.assertEqual([], model.plan_calls)
                    self.assertEqual("unsupported_scope", result.reason_code)
                    continue
                self.assertEqual(expected.get("context_used", False), result.context_used)
                self.assert_courses(result, list(expected["courses"]))
                self.assert_rules(result, expected["rules"])
                if expected.get("ambiguous"):
                    self.assertEqual("ambiguous", result.reason_code)
                    self.assertEqual([], model.write_calls)
                    for code in expected.get("candidate_codes", []):
                        self.assertIn(self.raw_courses[code]["course_name"], result.answer)
                    continue
                self.assertEqual(("generated", "generated", None), (result.plan_status, result.generation_status, result.reason_code))
                self.assertEqual(1, len(model.plan_calls))
                self.assertEqual(1, len(model.write_calls))
                for code, properties in expected["courses"].items():
                    if "count" not in properties:
                        self.assert_facets(result, code, properties)
                for category, count in expected.get("counts", {}).items():
                    self.assertEqual(count, len([raw for raw in self.raw_courses.values() if raw["category"] == category]))
                    packets = [part.course_evidence for part in result.parts if part.course_evidence and all(fact.category == category for fact in part.course_evidence.courses)]
                    self.assertEqual(1, len(packets))
                    self.assertCountEqual([raw["course_code"] for raw in self.raw_courses.values() if raw["category"] == category], [fact.course_code for fact in packets[0].courses])
                    self.assertRegex(result.answer, LABELS[category] + rf"[^.!?\n]{{0,60}}{count}\s*(?:개|과목)")
                if expected.get("obligation_code"):
                    code = expected["obligation_code"]
                    rule = self.raw_rules["cwnu.cs.2026.major-required-course-set"]
                    self.assertEqual(self.raw_courses[code]["course_name"], next(item["label"] for item in rule["decision"]["outcome"]["items"] if item["item_id"] == code))
                    self.assertRegex(result.answer, re.escape(self.raw_courses[code]["course_name"]) + r"[^.!?\n]{0,80}(?:반드시|이수해야)")
                if expected.get("overview"):
                    self.assertRegex(result.answer, r"졸업[^.!?\n]{0,50}130\s*학점")
                    self.assertRegex(result.answer, r"교양[^.!?\n]{0,50}34\s*학점")
                    self.assertRegex(result.answer, r"전공[^.!?\n]{0,50}78\s*학점")
                    self.assertRegex(result.answer, r"심층상담[^.!?\n]{0,70}1\s*회")
                    self.assertIn("권장", result.answer)
                    self.assertIn("Fail", result.answer)

    def test_campaign_has_exact_independent_24_predicates(self):
        expected = json.loads(EXPECTATIONS_PATH.read_text(encoding="utf-8"))
        questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
        self.assertEqual(24, len(questions))
        self.assertCountEqual([row["case_id"] for row in questions], expected["cases"])
        self.assertEqual({"repair": 12, "heldout": 12}, {split: sum(row["split"] == split for row in questions) for split in ("repair", "heldout")})

    def test_typed_missing_facets_fields_ids_and_provider_claims_fail_closed(self):
        good = course("컴퓨터구조")
        invalid_tools = [{k: v for k, v in good.items() if k != key} for key in ("purpose", "properties")]
        invalid_tools += [course("컴퓨터구조", properties=("credits", "credits")), course("컴퓨터구조", properties=("exemption",)), course("컴퓨터구조", purpose="approve"),
                          course("컴퓨터구조", year=True), course("컴퓨터구조", year=2.0), course("컴퓨터구조", semester=3), course("컴퓨터구조", category="foundation"),
                          course("qa-private@example.invalid"), course("MATCH(n)"), {**good, "approved": True}, {**good, "facts": [{"credits": 99}]},
                          dict(kind="rule", intent_ids=["cwnu.cs.2026.credits.major-total"]), dict(kind="rule", intent_ids=["credits.major.total", "credits.major.total"]),
                          dict(kind="requirements_overview", source_id="forged")]
        documents = [plan(tool) for tool in invalid_tools]
        documents += [dict(requests=[], context_used=False), plan(*([good] * 5)), {**plan(good), "typed_plan": True}, plan(good, context_used=True), dict(requests=[good], context_used=1)]
        for index, document in enumerate(documents):
            for boundary in ("facade", "api"):
                with self.subTest(case=index, boundary=boundary):
                    model = ControlledTypedProvider(document)
                    result = self.call(boundary, "컴구의 배정 정보를 알려줘", model)
                    self.assertEqual(("rejected", "processing_unavailable"), (result.plan_status, result.reason_code))
                    self.assertEqual([], model.write_calls)
                    self.assertNotEqual("supported", result.status)

    def test_typed_provider_result_provenance_cannot_be_downgraded_or_forged(self):
        document = plan(course("컴퓨터구조"))
        results = [SemanticDocument(document, typed_plan=False), SemanticDocument(document, typed_plan="true"), SemanticDocument(document, cached=1, typed_plan=True), document]
        for index, value in enumerate(results):
            for boundary in ("facade", "api"):
                with self.subTest(case=index, boundary=boundary):
                    model = ControlledTypedProvider(document)
                    model.result_override = value
                    result = self.call(boundary, "컴구 정보를 줘", model)
                    self.assertEqual(("rejected", "processing_unavailable"), (result.plan_status, result.reason_code))
                    self.assertEqual([], model.write_calls)

    def test_candidate_choice_and_bare_new_subject_preserve_all_provider_facets(self):
        cases = [("2번", "과목 후보 1 알고리즘 또는 2 소프트웨어공학 확인 요청 편성 학년 학기", "소프트웨어공학", "CDA0028", ["offering"]),
                 ("네프", "컴퓨터구조 학점 이수구분", "네트워크프로그래밍", "CDA0165", ["credits", "category"])]
        for question, previous, name, code, properties in cases:
            for boundary in ("facade", "api"):
                with self.subTest(case=code, boundary=boundary):
                    result = self.call(boundary, question, ControlledTypedProvider(plan(course(name, properties), context_used=True)), previous_question=previous)
                    self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                    self.assertTrue(result.context_used)
                    self.assert_courses(result, [code])
                    self.assert_facets(result, code, properties)
                    self.assertIn(name, result.context_question)
                    self.assertNotIn("컴퓨터구조", result.context_question)

    def test_contradictory_named_filter_preserves_hold_not_forged_classification(self):
        # Oracle correction: source says this named course is required, so a
        # typed elective filter has no matching row. The input-only contract
        # preserves filters; it does not mandate a genuine-missing-source label.
        # A processing hold with zero forged facts is a valid conservative
        # outcome. The old exact no_matching_evidence assumption was not sourced.
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary):
                model = ControlledTypedProvider(plan(course("컴퓨터구조", category="major_elective")))
                result = self.call(boundary, "선택 분류에 놓인 컴구의 수치만 확인해줘", model)
                self.assertEqual("major_required", self.raw_courses["CDA0016"]["category"])
                self.assertEqual(("insufficient_evidence", "generated", "processing_unavailable"), (result.status, result.plan_status, result.reason_code))
                self.assertEqual([], model.write_calls)
                self.assertEqual([], [fact for part in result.parts if part.course_evidence for fact in part.course_evidence.courses])
                self.assertEqual([], [cite for part in result.parts if part.course_evidence for cite in part.course_evidence.evidence])
                matching = self.call(boundary, "필수 분류에 놓인 컴구의 수치만 확인해줘", ControlledTypedProvider(plan(course("컴퓨터구조", category="major_required"))))
                self.assertEqual(("supported", "generated"), (matching.status, matching.generation_status))
                self.assert_courses(matching, ["CDA0016"])
                self.assert_facets(matching, "CDA0016", ["credits"])

    def test_description_evidence_gap_preserves_separate_known_attribute(self):
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary):
                result = self.call(boundary, "컴구에서 배우는 내용 및 인정량을 함께 설명해줘", ControlledTypedProvider(plan(course("컴퓨터구조", purpose="description"))))
                self.assertEqual(("insufficient_evidence", "no_matching_evidence"), (result.status, result.reason_code))
                self.assertIn("강의계획서", result.answer)
                self.assert_courses(result, ["CDA0016"])
                self.assert_facets(result, "CDA0016", ["credits"])

    def test_real_request_scope_and_previous_external_scope_precede_planner(self):
        updates = [dict(admission_year=2025), dict(matched_curriculum_year=2025), dict(department="기계공학과"), dict(previous_question="2025 교육과정의 졸업요건")]
        for change in updates:
            for boundary in ("facade", "api"):
                with self.subTest(change=change, boundary=boundary):
                    model = ControlledTypedProvider(plan(course("컴퓨터구조")))
                    result = self.call(boundary, "컴구 정보를 줘", model, **change)
                    self.assertEqual(("out_of_scope", "unsupported_scope"), (result.status, result.reason_code))
                    self.assertEqual([], model.plan_calls)

    def test_user_and_provider_pii_are_separate_generic_error_boundaries(self):
        from fastapi.testclient import TestClient
        from academic_assistant import api
        requests = [dict(question="qa-private@example.invalid 컴구 정보"), dict(question="컴구 정보", previous_question="학번 2026123456 학점")]
        for updates in requests:
            with self.subTest(updates=updates):
                model = ControlledTypedProvider(plan(course("컴퓨터구조")))
                payload = dict(question="컴구 정보", **SCOPE)
                payload.update(updates)
                with self.assertRaises(ValueError):
                    SemanticAssistant(self.engine, model).chat(AssistantTurnRequest.model_validate(payload))
                with patch.object(api, "_engine", return_value=self.engine), patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=model):
                    response = TestClient(api.app).post("/v1/academic/assistant", json=payload)
                self.assertEqual((422, {"detail": "invalid request"}), (response.status_code, response.json()))
                self.assertEqual([], model.plan_calls)

    def test_untrusted_writer_unknown_fact_ids_and_wrong_values_never_display(self):
        def changed_id(payload):
            document = projected_prose(payload)
            document["sections"][0]["fact_ids"] = ["cwnu.cs.2026.course.forged"]
            return document
        def changed_value(payload):
            document = projected_prose(payload)
            document["sections"][0]["text"] = "컴퓨터구조는 9학점입니다."
            return document
        for writer in (changed_id, changed_value):
            for boundary in ("facade", "api"):
                with self.subTest(writer=writer.__name__, boundary=boundary):
                    result = self.call(boundary, "컴구의 인정량을 줘", ControlledTypedProvider(plan(course("컴퓨터구조")), writer))
                    self.assertEqual(("fallback", "processing_unavailable"), (result.generation_status, result.reason_code))
                    self.assertNotIn("9학점", result.answer)
                    self.assertNotIn("forged", result.answer)
                    self.assert_courses(result, ["CDA0016"])

    def test_client_authored_plan_cannot_enter_public_wire(self):
        from fastapi.testclient import TestClient
        from academic_assistant import api
        model = ControlledTypedProvider(plan(course("컴퓨터구조")))
        payload = dict(question="컴구 인정량", plan=model.document, **SCOPE)
        with patch.object(api, "_engine", return_value=self.engine), patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=model):
            response = TestClient(api.app).post("/v1/academic/assistant", json=payload)
        self.assertEqual((422, {"detail": "invalid request"}), (response.status_code, response.json()))
        self.assertEqual([], model.plan_calls)

    def test_records_and_earned_metrics_are_not_transmitted_to_provider(self):
        transcript = dict(**SCOPE, confirmed=True, record_complete=True, degree_track="single_major", courses=[dict(row_id="QAPRIVATE0001", course_name="비공개교과", credits=7, grade="A0", category="free")])
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary):
                model = ControlledTypedProvider(plan(course("컴퓨터구조")))
                self.call(boundary, "컴구 정보를 줘", model, transcript=transcript, earned_credits={"credits.graduation.total": 117})
                wire = json.dumps(model.plan_calls + model.write_calls, ensure_ascii=False)
                for token in ("QAPRIVATE0001", "비공개교과", "earned_credits", "117"):
                    self.assertNotIn(token, wire)

    def test_review_scope_external_discipline_accusative_is_not_course_masked(self):
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary):
                model = ControlledTypedProvider(plan(dict(kind="requirements_overview")))
                result = self.call(boundary, "소프트웨어공학을 전공하는 학생의 졸업요건", model)
                self.assertEqual(("out_of_scope", "unsupported_scope"), (result.status, result.reason_code))
                self.assertEqual([], model.plan_calls)

    def test_review_scope_spaced_major_required_is_course_category(self):
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary):
                model = ControlledTypedProvider(plan(course("소프트웨어공학", ["category"])))
                result = self.call(boundary, "소프트웨어공학 전공 필수 과목인가요?", model)
                self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                self.assert_courses(result, ["CDA0028"])
                self.assert_facets(result, "CDA0028", ["category"])

    def test_review_scope_course_conjunction_is_not_department_suffix(self):
        for boundary in ("facade", "api"):
            with self.subTest(boundary=boundary):
                model = ControlledTypedProvider(plan(course("소프트웨어공학"), course("컴퓨터구조")))
                result = self.call(boundary, "소프트웨어공학과 컴퓨터구조 학점을 각각 알려줘", model)
                self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                self.assert_courses(result, ["CDA0028", "CDA0016"])
                self.assert_facets(result, "CDA0028", ["credits"])
                self.assert_facets(result, "CDA0016", ["credits"])

    def test_review_qualified_course_label_does_not_make_ordinary_words_pii(self):
        for question, category in (("전필은 과목 이름을 알려줘", "major_required"), ("과목 이름을 정리해 주세요", None)):
            filters = {"category": category} if category else {}
            codes = [code for code, raw in self.raw_courses.items() if category is None or raw["category"] == category]
            for boundary in ("facade", "api"):
                with self.subTest(question=question, boundary=boundary):
                    model = ControlledTypedProvider(plan(course(None, ["names"], **filters)))
                    result = self.call(boundary, question, model)
                    self.assertEqual(("supported", "generated"), (result.status, result.generation_status))
                    self.assert_courses(result, codes)
                    self.assertEqual(1, len(model.plan_calls))

    def test_review_instrumental_major_admission_or_transfer_precedes_planner(self):
        for question in ("소프트웨어공학으로 입학한 학생의 졸업요건", "소프트웨어공학으로 전과한 학생의 졸업요건"):
            for boundary in ("facade", "api"):
                with self.subTest(question=question, boundary=boundary):
                    model = ControlledTypedProvider(plan(dict(kind="requirements_overview")))
                    result = self.call(boundary, question, model)
                    self.assertEqual(("out_of_scope", "unsupported_scope"), (result.status, result.reason_code))
                    self.assertEqual([], model.plan_calls)

    def test_qualified_course_name_label_keeps_actual_personal_input_blocked(self):
        from fastapi.testclient import TestClient
        from academic_assistant import api
        for question in ("과목 이름 김민수", "과목 이름 홍길동", "과목 이름 학생 이름", "과목 이름 학번 2026123456", "과목 이름 qa-private@example.invalid"):
            with self.subTest(question=question):
                model = ControlledTypedProvider(plan(course(None, ["names"])))
                payload = dict(question=question, **SCOPE)
                with self.assertRaises(ValueError):
                    SemanticAssistant(self.engine, model).chat(AssistantTurnRequest.model_validate(payload))
                with patch.object(api, "_engine", return_value=self.engine), patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=model):
                    response = TestClient(api.app).post("/v1/academic/assistant", json=payload)
                self.assertEqual((422, {"detail": "invalid request"}), (response.status_code, response.json()))
                self.assertEqual([], model.plan_calls)


def _case_test(case_id):
    def test(self):
        self.campaign_case(case_id)
    return test


for _split in ("repair", "heldout"):
    for _index in range(1, 13):
        _id = f"b-input-{_split}-{_index:02d}"
        setattr(BInputIndependentQA, f"test_private_{_split}_{_index:02d}", _case_test(_id))


if __name__ == "__main__":
    unittest.main()
