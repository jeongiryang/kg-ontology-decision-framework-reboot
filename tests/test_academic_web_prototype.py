from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant import api
from academic_assistant.api import app
from academic_assistant.assistant_models import CourseFact
from academic_assistant.registry import Registry, canonical_sha256
from academic_assistant.semantic_llm import SemanticDocument


class AcademicWebPrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        settings = patch.dict(os.environ, {
            "ACADEMIC_EVIDENCE_BACKEND": "registry",
            "ACADEMIC_LLM_PROVIDER": "disabled",
            "ACADEMIC_LLM_GROUNDED_GENERATION": "0",
        })
        settings.start()
        cls.addClassCleanup(settings.stop)
        api._engine.cache_clear()
        api._chat_engine.cache_clear()
        cls.addClassCleanup(api._engine.cache_clear)
        cls.addClassCleanup(api._chat_engine.cache_clear)
        cls.registry = Registry.load(ROOT)
        cls.client = TestClient(app)
        cls.html = cls.client.get("/").text
        cls.css = cls.client.get("/assets/app.css").text
        cls.javascript = cls.client.get("/assets/app.js").text
        cls.semantic_javascript = cls.client.get("/assets/semantic-ui.js").text

    def test_fixed_page_and_asset_routes(self) -> None:
        page = self.client.get("/")
        styles = self.client.get("/assets/app.css")
        script = self.client.get("/assets/app.js")
        self.assertEqual(200, page.status_code)
        self.assertTrue(page.headers["content-type"].startswith("text/html"))
        self.assertTrue(styles.headers["content-type"].startswith("text/css"))
        self.assertTrue(script.headers["content-type"].startswith("text/javascript"))
        self.assertEqual(404, self.client.get("/assets/missing.js").status_code)
        self.assertEqual(404, self.client.get("/src/academic_assistant/api.py").status_code)

    def test_openapi_and_documentation_routes_remain_usable(self) -> None:
        openapi = self.client.get("/openapi.json")
        swagger = self.client.get("/docs")
        redoc = self.client.get("/redoc")
        self.assertEqual(200, openapi.status_code)
        self.assertEqual(200, swagger.status_code)
        self.assertEqual(200, redoc.status_code)
        paths = openapi.json()["paths"]
        self.assertIn("/v1/academic/answers", paths)
        self.assertIn("/v1/academic/chat", paths)
        self.assertIn("/v1/academic/assistant", paths)
        self.assertIn("/v1/academic/feedback", paths)
        self.assertIn("/readyz", paths)
        self.assertNotIn("/", paths)
        self.assertNotIn("/assets/app.css", paths)
        self.assertNotIn("/assets/app.js", paths)
        self.assertIn("SwaggerUIBundle", swagger.text)
        self.assertIn("redoc", redoc.text.lower())
        for response in (swagger, redoc):
            csp = response.headers["content-security-policy"]
            self.assertIn("https://cdn.jsdelivr.net", csp)
            self.assertIn("script-src 'unsafe-inline'", csp)
        self.assertNotIn("https://cdn.jsdelivr.net", self.client.get("/").headers["content-security-policy"])
        self.assertNotIn("'unsafe-inline'", openapi.headers["content-security-policy"])

    def test_security_headers_apply_to_page_assets_and_api(self) -> None:
        payload = {
            "schema_version": "1.0.0",
            "question": "졸업학점은 몇 학점인가요?",
            "admission_year": 2026,
            "matched_curriculum_year": 2026,
            "department": "컴퓨터공학과",
            "earned_credits": {},
        }
        responses = (
            self.client.get("/"),
            self.client.get("/assets/app.css"),
            self.client.get("/assets/app.js"),
            self.client.post("/v1/academic/answers", json=payload),
        )
        for response in responses:
            with self.subTest(url=str(response.url)):
                self.assertEqual("no-store", response.headers["cache-control"])
                self.assertEqual("nosniff", response.headers["x-content-type-options"])
                self.assertEqual("no-referrer", response.headers["referrer-policy"])
                csp = response.headers["content-security-policy"]
                self.assertIn("default-src 'none'", csp)
                self.assertIn("script-src 'self'", csp)
                self.assertIn("connect-src 'self'", csp)
                self.assertIn("frame-ancestors 'none'", csp)

    def test_page_is_korean_fixed_scope_and_accessible(self) -> None:
        self.assertIn('<html lang="ko">', self.html)
        self.assertIn("2026학번 · 2026 교육과정 · 컴퓨터공학과", self.html)
        self.assertIn('<label for="question">', self.html)
        self.assertIn('id="question-form"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('aria-busy="false"', self.html)
        self.assertIn('href="#question"', self.html)
        self.assertIn('tabindex="-1"', self.html)
        self.assertIn("Ctrl + Enter", self.html)
        self.assertEqual(11, self.html.count('data-credit-metric="'))
        self.assertIn('id="feedback-consent"', self.html)
        self.assertEqual(1, self.html.count("<textarea"))

    def test_request_shape_is_fixed_and_question_only(self) -> None:
        expected_fragments = (
            'admission_year: 2026',
            'matched_curriculum_year: 2026',
            'department: "컴퓨터공학과"',
            "earned_credits: earnedCredits",
            "function readCredits()",
            'fetch("/v1/academic/chat"',
            'method: "POST"',
        )
        for fragment in expected_fragments:
            self.assertIn(fragment, self.javascript)
        self.assertNotIn("student_name", self.javascript)
        self.assertNotIn("admission_year: question", self.javascript)

    def test_every_visible_example_is_supported_with_evidence(self) -> None:
        # Expectations come from the frozen approved records, not retrieval or
        # generated API output. Only the provider boundary is mocked.
        catalogue = self.registry.catalogue["courses"]
        structure = next(fact for fact in catalogue if fact["course_code"] == "CDA0016")
        thesis = next(fact for fact in catalogue if fact["course_code"] == "CDA0034")
        year_three = [fact for fact in catalogue if 3 in fact["offering_years"]]
        required = [fact for fact in catalogue if fact["category"] == "major_required"]
        elective = [fact for fact in catalogue if fact["category"] == "major_elective"]
        total = self.registry.rules["cwnu.cs.2026.credits.graduation-total"]
        retake = self.registry.rules["cwnu.cs.2026.course-counting.retake"]
        required_set = self.registry.rules["cwnu.cs.2026.major-required-course-set"]
        thesis_result = self.registry.rules["cwnu.cs.2026.graduation.thesis-completion-result"]
        self.assertEqual(3, structure["credits"])
        self.assertEqual(0, thesis["credits"])
        self.assertEqual((9, 34), (len(required), len(elective)))
        self.assertIn("CDA0034", {item["item_id"] for item in required_set["decision"]["outcome"]["items"]})

        def course_request(filters, properties, purpose="attributes"):
            return {"kind": "courses", "filters": filters, "purpose": purpose, "properties": properties}

        cases = {
            "컴퓨터구조는 몇 학점이야?": (
                [course_request({"name": structure["course_name"]}, ["credits"])],
                [("courses", [structure], f"{structure['course_name']}는 {structure['credits']}학점입니다.")],
            ),
            "3학년 과목 알려줘": (
                [course_request({"year": 3}, ["count", "names"])],
                [("courses", year_three, f"3학년 교육과정 목록은 {len(year_three)}과목입니다.")],
            ),
            "전필 과목 전선 과목 개수 몇 개야": (
                [course_request({"category": "major_required"}, ["count"]),
                 course_request({"category": "major_elective"}, ["count"])],
                [("courses", required, f"전공필수 목록은 {len(required)}과목입니다."),
                 ("courses", elective, f"전공선택 목록은 {len(elective)}과목입니다.")],
            ),
            "졸업학점 기준은 몇 학점인가요?": (
                [{"kind": "rule", "intent_ids": ["credits.graduation.total"]}],
                [("rules", [total], total["decision"]["statement"])],
            ),
            "재수강하면 학점이 중복 계산되나요?": (
                [{"kind": "rule", "intent_ids": ["course-counting.retake"]}],
                [("rules", [retake], retake["decision"]["statement"])],
            ),
            "졸업논문이 0학점이어도 필수인가요?": (
                [course_request({"name": thesis["course_name"]}, ["credits"]),
                 course_request({"name": thesis["course_name"]}, [], "completion_obligation")],
                [("courses", [thesis], f"{thesis['course_name']}은 {thesis['credits']}학점입니다."),
                 ("rules", [required_set], "졸업논문은 승인된 지정 이수과목이므로 반드시 이수해야 합니다."),
                 ("rules", [thesis_result], thesis_result["decision"]["statement"])],
            ),
        }
        examples = re.findall(r'data-question="([^"]+)"', self.html)
        self.assertEqual(list(cases), examples)
        self.assertIn('fetch("/v1/academic/assistant"', self.semantic_javascript)
        for example in examples:
            with self.subTest(example=example):
                requests, expected_parts = cases[example]
                sections = [{"part_id": f"p{index}", "text": text,
                             "fact_ids": [fact["course_id" if family == "courses" else "rule_id"] for fact in facts]}
                            for index, (family, facts, text) in enumerate(expected_parts, 1)]
                provider = MagicMock()
                session = provider.session.return_value.__enter__.return_value
                session.plan.return_value = SemanticDocument(
                    {"requests": requests, "context_used": False}, typed_plan=True)
                session.write.return_value = SemanticDocument({"sections": sections})
                payload = {
                    "schema_version": "1.0.0",
                    "question": example,
                    "admission_year": 2026,
                    "matched_curriculum_year": 2026,
                    "department": "컴퓨터공학과",
                    "earned_credits": {},
                }
                with patch("academic_assistant.assistant.SemanticLLMClient.from_env", return_value=provider):
                    response = self.client.post("/v1/academic/assistant", json=payload)
                self.assertEqual(200, response.status_code)
                body = response.json()
                self.assertEqual("supported", body["status"])
                self.assertEqual("generated", body["plan_status"])
                self.assertEqual("generated", body["generation_status"])
                self.assertIsNone(body["reason_code"])
                session.plan.assert_called_once()
                session.write.assert_called_once()
                session.accept.assert_called_once()
                planned_payload = session.plan.call_args.args[0]
                self.assertEqual(example, planned_payload["question"])
                self.assertIsNone(planned_payload["previous_question"])
                self.assertFalse(planned_payload["has_transcript"])
                self.assertNotIn("earned_credits", planned_payload)
                self.assertEqual([section["fact_ids"] for section in sections],
                                 [[fact["fact_id"] for fact in part["facts"]]
                                  for part in session.write.call_args.args[0]["parts"]])
                self.assertEqual(len(expected_parts), len(body["parts"]))
                for part, (family, facts, text) in zip(body["parts"], expected_parts, strict=True):
                    self.assertEqual("supported", part["status"])
                    self.assertIn(text, part["text"])
                    self.assertIn(text, body["answer"])
                    packet = part["course_evidence" if family == "courses" else "evidence_packet"]
                    self.assertEqual("supported", packet["status"])
                    self.assertEqual({"admission_year": 2026, "matched_curriculum_year": 2026,
                                      "department": "컴퓨터공학과"}, packet["scope"])
                    self.assertEqual([], packet["issues"])
                    if family == "courses":
                        self.assertEqual([CourseFact.model_validate(fact).model_dump() for fact in facts], packet["courses"])
                        categories = {"major_required": "전공필수", "major_elective": "전공선택"}
                        self.assertEqual([{"course_id": fact["course_id"], "source_id": fact["source_id"],
                                           "source_sha256": fact["source_sha256"], "locator": fact["locator"],
                                           "claim": f"{fact['course_name']}: {categories[fact['category']]}, "
                                                    f"{fact['credits']}학점, {fact['offering_label']}."}
                                          for fact in facts], packet["evidence"])
                        for fact in facts:
                            self.assertEqual(fact["fact_sha256"], canonical_sha256(
                                {key: value for key, value in fact.items() if key != "fact_sha256"}))
                    else:
                        self.assertEqual([{"rule_id": fact["rule_id"], "rule_sha256": canonical_sha256(fact)}
                                          for fact in facts], packet["applied_rules"])
                        self.assertEqual([{"source_id": cite["source_id"], "rule_id": fact["rule_id"],
                                           "locator": cite["locator"], "claim": fact["decision"]["statement"]}
                                          for fact in facts for cite in fact["evidence"]], packet["evidence"])

    def test_legacy_rule_examples_remain_supported_with_exact_evidence(self) -> None:
        for example, rule_id in (
            ("졸업학점 기준은 몇 학점인가요?", "cwnu.cs.2026.credits.graduation-total"),
            ("재수강하면 학점이 중복 계산되나요?", "cwnu.cs.2026.course-counting.retake"),
        ):
            with self.subTest(example=example):
                rule = self.registry.rules[rule_id]
                response = self.client.post("/v1/academic/answers", json={
                    "question": example, "admission_year": 2026, "matched_curriculum_year": 2026,
                    "department": "컴퓨터공학과", "earned_credits": {},
                })
                self.assertEqual(200, response.status_code)
                body = response.json()
                self.assertEqual("supported", body["status"])
                self.assertIn(rule["decision"]["statement"], body["answer"])
                self.assertEqual([{"rule_id": rule_id, "rule_sha256": canonical_sha256(rule)}],
                                 body["evidence_packet"]["applied_rules"])
                self.assertEqual([{"source_id": cite["source_id"], "rule_id": rule_id,
                                   "locator": cite["locator"], "claim": rule["decision"]["statement"]}
                                  for cite in rule["evidence"]], body["evidence_packet"]["evidence"])

    def test_every_answer_status_has_distinct_presentation(self) -> None:
        expected = {
            "supported": "근거 확인",
            "insufficient_evidence": "근거 부족",
            "conflict": "규칙 충돌",
            "out_of_scope": "지원 범위 밖",
        }
        for status, label in expected.items():
            with self.subTest(status=status):
                self.assertIn(f'{status}: "{label}"', self.javascript)
                self.assertIn(f".status-{status}", self.css)

    def test_response_content_uses_safe_dom_construction(self) -> None:
        self.assertIn('document.createElement("li")', self.javascript)
        self.assertIn("title.textContent = primary", self.javascript)
        self.assertIn("evidence.locator", self.javascript)
        self.assertIn("packet.applied_rules", self.javascript)
        self.assertIn("packet.issues", self.javascript)
        self.assertIn("renderCalculations", self.javascript)
        self.assertNotIn("innerHTML", self.javascript)
        self.assertNotIn("insertAdjacentHTML", self.javascript)
        self.assertNotIn("document.write", self.javascript)

    def test_no_automatic_browser_persistence_or_external_assets(self) -> None:
        combined = self.html + self.javascript
        for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "google-analytics", "http://", "https://"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)
        self.assertNotIn(".pdf", combined.lower())
        self.assertIn('cache: "no-store"', self.javascript)
        self.assertIn("질문 원문은 저장하지 않습니다.", self.html)

    def test_credit_inputs_send_only_entered_metrics_and_calculate_gap(self) -> None:
        self.assertIn('data-credit-metric="credits.graduation.total"', self.html)
        self.assertIn("credits[input.dataset.creditMetric] = value", self.javascript)
        response = self.client.post("/v1/academic/answers", json={
            "schema_version": "1.0.0",
            "question": "졸업학점 얼마나 부족해",
            "admission_year": 2026,
            "matched_curriculum_year": 2026,
            "department": "컴퓨터공학과",
            "earned_credits": {"credits.graduation.total": 120},
        })
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("supported", body["status"])
        self.assertEqual([{"metric": "credits.graduation.total", "required": 130, "earned": 120, "gap": 10}], body["calculations"])

    def test_feedback_requires_explicit_consent_and_stores_only_locally(self) -> None:
        self.assertIn('fetch("/v1/academic/feedback"', self.javascript)
        self.assertIn("개인정보가 없는 질문임을 확인했습니다.", self.html)
        question = "PCCP 기준은 무엇인가요?"
        answer = self.client.post("/v1/academic/answers", json={
            "schema_version": "1.0.0",
            "question": question,
            "admission_year": 2026,
            "matched_curriculum_year": 2026,
            "department": "컴퓨터공학과",
            "earned_credits": {},
        }).json()
        payload = {
            "schema_version": "1.0.0",
            "packet_id": answer["packet_id"],
            "status": "insufficient_evidence",
            "question": question,
            "earned_credits": {},
            "category": "missing_evidence",
            "consent_to_store": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "feedback.jsonl"
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(destination),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(Path(directory)),
            }):
                response = self.client.post("/v1/academic/feedback", json=payload)
            self.assertEqual(201, response.status_code)
            body = response.json()
            self.assertTrue(body["stored"])
            self.assertNotIn("question", body)
            records = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(1, len(records))
            self.assertEqual("2.0.0", records[0]["schema_version"])
            self.assertNotIn("question", records[0])
            self.assertNotIn(payload["question"], destination.read_text(encoding="utf-8"))
            self.assertEqual("컴퓨터공학과", records[0]["scope"]["department"])

            before = destination.read_text(encoding="utf-8")
            rejected = {**payload, "consent_to_store": False}
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(destination),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(Path(directory)),
            }):
                self.assertEqual(422, self.client.post("/v1/academic/feedback", json=rejected).status_code)
                tampered = self.client.post("/v1/academic/feedback", json={**payload, "packet_id": "academic-" + "f" * 32})
            self.assertEqual(422, tampered.status_code)
            self.assertEqual(before, destination.read_text(encoding="utf-8"))

    def test_feedback_rejects_supported_status_and_identifying_content(self) -> None:
        base = {
            "schema_version": "1.0.0",
            "packet_id": "academic-" + "b" * 32,
            "status": "insufficient_evidence",
            "question": "캡스톤 기준은 무엇인가요?",
            "earned_credits": {},
            "category": "missing_evidence",
            "consent_to_store": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "feedback.jsonl"
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(destination),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(Path(directory)),
            }):
                supported = self.client.post("/v1/academic/feedback", json={**base, "status": "supported"})
                identifying = self.client.post("/v1/academic/feedback", json={**base, "question": "학번 2026123456의 캡스톤 기준"})
            self.assertEqual(422, supported.status_code)
            self.assertEqual(422, identifying.status_code)
            self.assertFalse(destination.exists())

    def test_protected_boundaries_are_visible_and_remain_fail_closed(self) -> None:
        for topic in ("PCCP", "캡스톤", "공모전", "졸업작품"):
            self.assertIn(topic, self.html)
            payload = {
                "schema_version": "1.0.0",
                "question": f"{topic} 기준을 알려줘",
                "admission_year": 2026,
                "matched_curriculum_year": 2026,
                "department": "컴퓨터공학과",
                "earned_credits": {},
            }
            response = self.client.post("/v1/academic/answers", content=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
            self.assertEqual(200, response.status_code)
            body = response.json()
            self.assertEqual("insufficient_evidence", body["status"])
            self.assertEqual([], body["evidence_packet"]["evidence"])

    def test_packaged_web_assets_are_importable_resources(self) -> None:
        web = files("academic_assistant").joinpath("web")
        for name in ("index.html", "app.css", "app.js"):
            asset = web.joinpath(name)
            self.assertTrue(asset.is_file())
            self.assertGreater(len(asset.read_bytes()), 100)


if __name__ == "__main__":
    unittest.main()
