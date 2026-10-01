"""Anonymous demo integration boundaries; no real graph/model or tunnel work."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

from academic_assistant import api
from academic_assistant.core import AnswerEngine
from academic_assistant.grounded_chat import GroundedChatEngine
from academic_assistant.public_demo import DemoLimits, PublicDemo, create_demo_app
from academic_assistant.registry import Registry, RegistryUnavailable, canonical_sha256
from tests.test_evidence_pdf import RULE, SOURCE, pdf_bytes, red_pixels, registry_for


ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"
METRIC = "credits.graduation.total"


def question(text="졸업학점은 얼마인가요?", **updates):
    value = {"question": text, "admission_year": 2026,
             "matched_curriculum_year": 2026, "department": "컴퓨터공학과",
             "earned_credits": {}}
    value.update(updates)
    return value


def transcript(credits=3, **updates):
    value = {"admission_year": 2026, "matched_curriculum_year": 2026,
             "department": "컴퓨터공학과", "degree_track": "single_major",
             "confirmed": True, "record_complete": True,
             "courses": [{"row_id": "row-1", "course_name": "가상 선택 과목",
                          "credits": credits, "grade": "A0", "category": "free"}]}
    value.update(updates)
    return value


class PublicAPIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Public global caches must never inherit another test's private graph.
        api._chat_engine.cache_clear()
        api._engine.cache_clear()
        self.addCleanup(api._engine.cache_clear)
        self.addCleanup(api._chat_engine.cache_clear)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix="public-api-integration-")))
        bootstrap = {key: value for key, value in os.environ.items()
                     if key.lower() in {"systemroot", "windir", "temp", "tmp"}}
        self.stack.enter_context(patch.dict(os.environ, {**bootstrap,
            "ACADEMIC_PUBLIC_DEMO": "1",
            "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(self.directory),
            "ACADEMIC_FEEDBACK_PATH": str(self.directory / "feedback.jsonl"),
            "NEO4J_PASSWORD": "qa-credential-marker",
            "ACADEMIC_LLM_URL": "http://qa-private-model.invalid",
        }, clear=True))
        self.stack.enter_context(patch.object(api.Neo4jEvidenceReader, "from_env", return_value=None))
        self.stack.enter_context(patch("academic_assistant.llm.LocalLLMClient.from_env", return_value=None))
        self.store = self.stack.enter_context(patch.object(api, "store_feedback"))
        self.registry = Registry.load(ROOT)
        self.demo = create_demo_app()
        self.demo.limits = replace(DemoLimits(), posts_per_minute=100,
                                   requests_per_minute=250, previews_per_minute=100)
        self.client = self.stack.enter_context(TestClient(self.demo, base_url=BASE))

    def assert_safe(self, response):
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertNotIn("set-cookie", response.headers)
        for value in ("qa-credential-marker", "qa-private-model.invalid", str(ROOT), str(self.directory)):
            self.assertNotIn(value, response.text)

    def assert_supported_packet(self, packet):
        self.assertEqual("supported", packet["status"])
        self.assertEqual({"admission_year": 2026, "matched_curriculum_year": 2026,
                          "department": "컴퓨터공학과"}, packet["scope"])
        self.assertEqual([], packet["issues"])
        self.assertTrue(packet["applied_rules"])
        self.assertTrue(packet["evidence"])
        rule_ids = {entry["rule_id"] for entry in packet["applied_rules"]}
        self.assertEqual(rule_ids, {entry["rule_id"] for entry in packet["evidence"]})
        for applied in packet["applied_rules"]:
            rule = self.registry.rules[applied["rule_id"]]
            self.assertEqual(self.registry.rule_hashes[applied["rule_id"]], applied["rule_sha256"])
            self.assertEqual(canonical_sha256(rule), applied["rule_sha256"])
            self.assertEqual(("human", "full", "approved"), tuple(rule["review"][key]
                             for key in ("mode", "scope", "status")))
            for key in ("curriculum_years", "admission_years"):
                self.assertEqual([2026], rule["applicability"][key])
            self.assertEqual(["컴퓨터공학과"], rule["applicability"]["departments"])
        for reference in packet["evidence"]:
            rule = self.registry.rules[reference["rule_id"]]
            self.assertEqual(rule["decision"]["statement"], reference["claim"])
            self.assertIn((reference["source_id"], reference["locator"]),
                          {(item["source_id"], item["locator"]) for item in rule["evidence"]})
            source = self.registry.sources[reference["source_id"]]
            self.assertEqual(("human", "full", "approved"), tuple(source["review"][key]
                             for key in ("mode", "scope", "status")))
            self.assertRegex(reference["locator"], r"PDF p\.\d+")

    def test_supported_chat_and_answer_keep_exact_approved_citations(self):
        answer = self.client.post("/v1/academic/answers", json=question())
        chat = self.client.post("/v1/academic/chat", json=question())
        for response in (answer, chat):
            self.assertEqual(200, response.status_code)
            body = response.json()
            self.assertEqual("supported", body["status"])
            self.assertIn("130", body["answer"])
            self.assert_supported_packet(body["evidence_packet"])
            self.assertEqual(["cwnu.cs.2026.credits.graduation-total"],
                             [item["rule_id"] for item in body["evidence_packet"]["applied_rules"]])
            for reference in body["evidence_packet"]["evidence"]:
                self.assertIn(reference["claim"], body["answer"])
            self.assert_safe(response)
        for key, value in answer.json().items():
            self.assertEqual(value, chat.json()[key])
        self.assertEqual("skipped", chat.json()["llm_status"])

    def test_unknown_pending_exception_and_wrong_scope_remain_unsupported(self):
        cases = [(question("휴학할 때 절차가 궁금합니다"), "insufficient_evidence"),
                 (question("PCCP 인증을 몇 학기에 제출해야 하나요?"), "insufficient_evidence"),
                 (question("심화전공 면제자는 몇 학점을 이수해야 하나요?"), "insufficient_evidence"),
                 (question("졸업학점 얼마나 부족해"), "insufficient_evidence"),
                 (question(admission_year=2025), "out_of_scope"),
                 (question(matched_curriculum_year=2025), "out_of_scope"),
                 (question(department="전자공학과"), "out_of_scope")]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                response = self.client.post("/v1/academic/chat", json=payload)
                self.assertEqual(200, response.status_code)
                body = response.json()
                self.assertEqual(expected, body["status"])
                self.assertEqual(expected, body["evidence_packet"]["status"])
                self.assertEqual([], body["evidence_packet"]["applied_rules"])
                self.assertEqual([], body["evidence_packet"]["evidence"])
                self.assertTrue(body["evidence_packet"]["issues"])
                self.assert_safe(response)

    def test_conflicting_input_registry_remains_conflict_at_public_boundary(self):
        conflicted = replace(self.registry, conflicts={"credit_threshold:" + METRIC:
                             ("cwnu.cs.2026.credits.graduation-total", "fixture.conflicting-rule")})
        with patch.object(api, "_chat_engine", return_value=GroundedChatEngine(AnswerEngine(conflicted))):
            response = self.client.post("/v1/academic/chat", json=question())
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("conflict", body["status"])
        self.assertEqual("conflict", body["evidence_packet"]["status"])
        self.assertEqual([], body["evidence_packet"]["applied_rules"])
        self.assertEqual([], body["evidence_packet"]["evidence"])
        self.assert_safe(response)

    def test_two_anonymous_clients_cannot_reuse_each_others_credit_facts(self):
        other = self.stack.enter_context(TestClient(self.demo, base_url=BASE))
        for client, credits, gap in ((self.client, 100, 30), (other, 120, 10)):
            response = client.post("/v1/academic/chat", json=question(
                "졸업학점 얼마나 부족해", earned_credits={METRIC: credits}))
            self.assertEqual(200, response.status_code)
            body = response.json()
            self.assert_supported_packet(body["evidence_packet"])
            self.assertEqual([{ "metric": METRIC, "required": 130,
                              "earned": credits, "gap": gap}], body["calculations"])
            self.assertEqual({METRIC: credits}, body["evidence_packet"]["student_facts"])
        for client in (other, self.client):
            response = client.post("/v1/academic/chat", json=question("졸업학점 얼마나 부족해"))
            self.assertEqual("insufficient_evidence", response.json()["status"])
            self.assertEqual({}, response.json()["evidence_packet"]["student_facts"])
            self.assertEqual([], response.json()["calculations"])
        self.assertFalse(self.client.cookies)
        self.assertFalse(other.cookies)

    def test_transcript_partial_comparison_is_confirmed_and_request_local(self):
        other = self.stack.enter_context(TestClient(self.demo, base_url=BASE))
        for client, credits in ((self.client, 3), (other, 9), (self.client, 3)):
            response = client.post("/v1/academic/transcripts/assess", json=transcript(credits))
            self.assertEqual(200, response.status_code)
            body = response.json()
            self.assertEqual("insufficient_evidence", body["status"])
            self.assertEqual(credits, body["raw_earned_credits"])
            self.assertEqual(credits, body["recognized_graduation_credits"])
            self.assertFalse(body["official_graduation_decision"])
            self.assertEqual("partial_comparison", body["credit_summary"]["recognition_status"])
            total = next(item for item in body["checks"] if item["check_id"] == METRIC)
            self.assertEqual((130, credits, 130 - credits),
                             (total["required"], total["earned"], total["gap"]))
            self.assert_supported_packet(total["evidence_packet"])
            self.assert_safe(response)
        rejected = other.post("/v1/academic/transcripts/assess", json=transcript(confirmed=False))
        self.assertEqual(422, rejected.status_code)
        self.assertEqual({"detail": "invalid request"}, rejected.json())
        self.assertEqual([], list(self.directory.iterdir()))

    def test_transcript_followup_requires_its_own_confirmed_input(self):
        response = self.client.post("/v1/academic/transcripts/chat", json={
            "question": "졸업학점 얼마나 부족해?", "transcript": transcript()})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("supported", body["status"])
        self.assertEqual(127, next(item for item in body["selected_checks"]
                                  if item["check_id"] == METRIC)["gap"])
        for check in body["selected_checks"]:
            self.assert_supported_packet(check["evidence_packet"])
        self.assertIn("최종 졸업 인증은 아닙니다", body["answer"])
        for payload in ({"question": "졸업학점 부족해?"},
                        {"question": "졸업학점 부족해?", "transcript": transcript(confirmed=False)}):
            self.assertEqual(422, self.client.post("/v1/academic/transcripts/chat", json=payload).status_code)
        scoped = self.client.post("/v1/academic/transcripts/chat", json={
            "question": "졸업학점 부족해?", "transcript": transcript(admission_year=2025)})
        self.assertEqual("out_of_scope", scoped.json()["status"])
        self.assertEqual([], scoped.json()["selected_checks"])

    def test_identifying_and_invalid_input_is_sanitized_without_file_records(self):
        marker = "2026123456"
        cases = [("/v1/academic/chat", question("내 학번 " + marker + " 기준 졸업학점은?")),
                 ("/v1/academic/answers", question(admission_year=True)),
                 ("/v1/academic/chat", question(previous_question="학번 " + marker)),
                 ("/v1/academic/chat", question(student_id=marker)),
                 ("/v1/academic/chat", question(earned_credits={METRIC: -1})),
                 ("/v1/academic/transcripts/assess", transcript(student_id=marker)),
                 ("/v1/academic/transcripts/assess", transcript(courses=[{
                     "row_id": "row-1", "course_name": "학번" + marker,
                     "credits": 3, "grade": "A0", "category": "free"}])),
                 ("/v1/academic/transcripts/chat", {
                     "question": "학번 " + marker + " 졸업학점?", "transcript": transcript()})]
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            for path, payload in cases:
                with self.subTest(path=path, payload=payload):
                    response = self.client.post(path, json=payload)
                    self.assertEqual(422, response.status_code)
                    self.assertEqual({"detail": "invalid request"}, response.json())
                    self.assertNotIn(marker, response.text)
                    self.assert_safe(response)
        self.assertNotIn(marker, output.getvalue())
        self.store.assert_not_called()
        self.assertEqual([], list(self.directory.iterdir()))

    def test_private_routes_and_encoded_aliases_do_not_reach_storage(self):
        for path in ("/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json",
                     "/.local/prototype.json", "/v1/academic/feedback", "/v1/models",
                     "/metrics", "/assets/../api.py", "/%2e%2e/.local/prototype.json",
                     "/%64ocs", "/v1/academic/transcripts/records"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(404, response.status_code)
                self.assert_safe(response)
        feedback = self.client.post("/v1/academic/feedback", json={
            "schema_version": "1.0.0", "packet_id": "academic-" + "a" * 32,
            "status": "insufficient_evidence", "question": "휴학 절차",
            "earned_credits": {}, "category": "missing_evidence", "consent_to_store": True})
        self.assertEqual(404, feedback.status_code)
        self.store.assert_not_called()
        self.assertEqual([], list(self.directory.iterdir()))

    def test_public_marker_inventory_and_assets_preserve_security(self):
        marker = self.client.get("/v1/academic/demo")
        self.assertEqual({"public": True, "authentication": False,
                          "transcript_storage": "request_memory", "provider": "cloudflare_quick_tunnel"}, marker.json())
        ready = self.client.get("/readyz")
        self.assertEqual(200, ready.status_code)
        self.assertEqual({"status": "ready", "public_demo": True}, ready.json())
        inventory = self.client.get("/v1/academic/runtime")
        self.assertEqual(200, inventory.status_code)
        self.assertFalse(inventory.json()["llm_model_available"])
        self.assertFalse(inventory.json()["student_records_sent_to_llm"])
        for path in ("/", "/assets/app.js", "/assets/transcript.js", "/assets/evidence.js"):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code)
            self.assert_safe(response)
            self.assertEqual("DENY", response.headers["x-frame-options"])
            self.assertIn("default-src 'none'", response.headers["content-security-policy"])
            self.assertIn("noarchive", response.headers["x-robots-tag"])
        for response in (marker, ready, inventory):
            self.assert_safe(response)

    def test_readiness_and_evidence_configuration_fail_closed_without_paths(self):
        missing = self.client.get("/v1/academic/evidence/fixture.unknown.rule/preview")
        self.assertEqual(404, missing.status_code)
        approved = self.client.get("/v1/academic/evidence/cwnu.cs.2026.credits.graduation-total/preview")
        self.assertEqual(503, approved.status_code)
        self.assert_safe(approved)
        api._chat_engine.cache_clear()
        api._engine.cache_clear()
        with patch.object(api.Registry, "load", side_effect=RegistryUnavailable()):
            response = self.client.get("/readyz")
            self.assertEqual(503, response.status_code)
            self.assertNotIn("public_demo", response.json())
            self.assert_safe(response)

    def test_public_preview_serves_exact_synthetic_citation_without_source_path(self):
        # Render through the real API router and bounded child using only a tiny
        # generated, human/full/approved fixture, never a private academic PDF.
        data = pdf_bytes([[('Approved graduation requirement', 50, 650)]])
        source = self.directory / "approved.pdf"
        source.write_bytes(data)
        mapping = self.directory / "source-map.json"
        mapping.write_text(json.dumps({SOURCE: str(source)}), encoding="utf-8")
        api._engine.cache_clear()
        with patch.object(api.Registry, "load", return_value=registry_for(data)), \
                patch.dict(os.environ, {"ACADEMIC_SOURCE_MAP": str(mapping)}):
            response = self.client.get(f"/v1/academic/evidence/{RULE}/preview")
            self.assertEqual(200, response.status_code)
            metadata = response.json()
            self.assertEqual("exact", metadata["precision"])
            self.assertEqual((1, 9), (metadata["pdf_page"], metadata["printed_page"]))
            self.assertEqual("Approved graduation requirement", metadata["quote"])
            self.assert_safe(response)
            image = self.client.get(metadata["image_url"])
            self.assertEqual(200, image.status_code)
            self.assertEqual("image/png", image.headers["content-type"])
            self.assertTrue(red_pixels(image.content))
            download = self.client.get(metadata["pdf_url"])
            self.assertEqual(200, download.status_code)
            self.assertEqual("application/pdf", download.headers["content-type"])
            self.assertTrue(download.content.startswith(b"%PDF-"))
        self.assertEqual(data, source.read_bytes())

    def test_actual_api_sync_deadline_retains_work_capacity_until_worker_returns(self):
        started, release, completed = threading.Event(), threading.Event(), threading.Event()
        facade = GroundedChatEngine(AnswerEngine(self.registry))

        class BlockingFacade:
            def chat(self, request):
                started.set()
                if not release.wait(timeout=2):
                    raise RuntimeError("test work did not receive release")
                result = facade.chat(request)
                completed.set()
                return result

        async def exercise():
            demo = PublicDemo(api.app, replace(DemoLimits(), work_seconds=.08, work_concurrent=1))
            sent = []

            async def observe(scope, receive, send):
                async def capture(event):
                    sent.append(dict(event))
                    await send(event)
                await demo(scope, receive, capture)

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe), base_url=BASE) as client:
                before = time.monotonic()
                response = await client.post("/v1/academic/chat", json=question())
                elapsed = time.monotonic() - before
                self.assertTrue(started.is_set())
                self.assertEqual(503, response.status_code)
                self.assertLess(elapsed, .4)
                self.assertEqual({"detail": "request unavailable"}, response.json())
                self.assertFalse(completed.is_set())
                self.assertEqual((1, 1), (demo.active, demo.work_active))
                self.assert_safe(response)
                count = len(sent)
                blocked = await client.post("/v1/academic/chat", json=question())
                self.assertEqual(429, blocked.status_code)
                self.assertEqual("60", blocked.headers["retry-after"])
                self.assertEqual(1, demo.work_active)
                count = len(sent)
                release.set()
                deadline = time.monotonic() + 1
                while demo.work_active and time.monotonic() < deadline:
                    await asyncio.sleep(.01)
                self.assertTrue(completed.is_set())
                self.assertEqual((0, 0), (demo.active, demo.work_active))
                self.assertEqual(count, len(sent), "late backend body reached the caller")
                recovery = await client.post("/v1/academic/chat", json=question())
                self.assertEqual(200, recovery.status_code)
                self.assertEqual("supported", recovery.json()["status"])
                self.assert_safe(recovery)

        with patch.object(api, "_chat_engine", return_value=BlockingFacade()):
            try:
                asyncio.run(exercise())
            finally:
                release.set()


if __name__ == "__main__":
    unittest.main()
