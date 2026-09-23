from __future__ import annotations

import json
import re
import sys
import unittest
from importlib.resources import files
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.api import app


class AcademicWebPrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.html = cls.client.get("/").text
        cls.css = cls.client.get("/assets/app.css").text
        cls.javascript = cls.client.get("/assets/app.js").text

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
        self.assertNotIn("<input", self.html)
        self.assertEqual(1, self.html.count("<textarea"))

    def test_request_shape_is_fixed_and_question_only(self) -> None:
        expected_fragments = (
            'admission_year: 2026',
            'matched_curriculum_year: 2026',
            'department: "컴퓨터공학과"',
            'earned_credits: {}',
            'fetch("/v1/academic/answers"',
            'method: "POST"',
        )
        for fragment in expected_fragments:
            self.assertIn(fragment, self.javascript)
        self.assertNotIn("student_name", self.javascript)
        self.assertNotIn("admission_year: question", self.javascript)

    def test_every_visible_example_is_supported_with_evidence(self) -> None:
        examples = re.findall(r'data-question="([^"]+)"', self.html)
        self.assertGreaterEqual(len(examples), 1)
        self.assertEqual("졸업학점 기준은 몇 학점인가요?", examples[0])
        for example in examples:
            with self.subTest(example=example):
                response = self.client.post("/v1/academic/answers", json={
                    "schema_version": "1.0.0",
                    "question": example,
                    "admission_year": 2026,
                    "matched_curriculum_year": 2026,
                    "department": "컴퓨터공학과",
                    "earned_credits": {},
                })
                self.assertEqual(200, response.status_code)
                body = response.json()
                self.assertEqual("supported", body["status"])
                self.assertTrue(body["evidence_packet"]["applied_rules"])
                self.assertTrue(body["evidence_packet"]["evidence"])

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

    def test_no_persistence_or_external_assets(self) -> None:
        combined = self.html + self.javascript
        for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "google-analytics", "http://", "https://"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)
        self.assertNotIn(".pdf", combined.lower())
        self.assertIn('cache: "no-store"', self.javascript)

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
