"""Independent synthetic checks at API and supervisor boundaries (no services)."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import socketserver
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry as SchemaRegistry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "operations"))

import academic_assistant.api as api
from academic_assistant.core import AnswerEngine
from academic_assistant.grounded_chat import GroundedChatEngine
from academic_assistant.registry import Registry, RegistryUnavailable
from scripts.validation.validate_academic_knowledge import validate_evidence_packet
import prototype_supervisor as supervisor


class NoInference:
    def suggest_intent(self, *_args):
        raise AssertionError("Independent QA must not invoke a model")


def chat_payload(question, **updates):
    value = dict(question=question, admission_year=2026,
                 matched_curriculum_year=2026, department="컴퓨터공학과")
    value.update(updates)
    return value


def row(row_id, name="합성 선택과목", **updates):
    value = dict(row_id=row_id, course_name=name, credits=3,
                 grade="A0", category="free")
    value.update(updates)
    return value


def transcript(courses=None, **updates):
    value = dict(admission_year=2026, matched_curriculum_year=2026,
                 department="컴퓨터공학과", degree_track="single_major",
                 confirmed=True, record_complete=True,
                 courses=courses if courses is not None else [row("synthetic-1")])
    value.update(updates)
    return value


@contextmanager
def diagnostic_server(*, slow=False):
    """Bind a new loopback port and dispose only this test's own server."""
    responses = {
        "/readyz": (200, b'{"status":"ready"}'),
        "/v1/academic/runtime": (200, b'{"evidence_backend":"neo4j","graph_verified":true,"llm_configured":false}'),
    }
    received = []
    stop_stream = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.command, self.path))
            status, body = responses.get(self.path, (404, b"{}"))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if slow:
                    for byte in body:
                        if stop_stream.wait(0.02):
                            break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    if server.server_port == 8000:
        server.server_close()
        raise AssertionError("Ephemeral fixture cannot use the current prototype port")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(supervisor, "BASE_URL", f"http://127.0.0.1:{server.server_port}"):
            yield responses, received
    finally:
        stop_stream.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def raw_diagnostic_server(response, *, keep_open=False):
    stop = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(0.5)
            try:
                self.request.recv(1024)
                self.request.sendall(response)
                if keep_open:
                    stop.wait(0.5)
            except OSError:
                pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    if server.server_address[1] == 8000:
        server.server_close()
        raise AssertionError("Ephemeral fixture cannot use the current prototype port")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(supervisor, "BASE_URL", f"http://127.0.0.1:{server.server_address[1]}"):
            yield
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class IndependentAcademicApiQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load(ROOT)
        local_schemas = [json.loads(path.read_text(encoding="utf-8"))
                         for path in (ROOT / "contracts").glob("*.schema.json")]
        schema_registry = SchemaRegistry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in local_schemas)
        cls.schemas = {
            key: Draft202012Validator(json.loads(
                (ROOT / "contracts" / f"{key}.schema.json").read_text(encoding="utf-8")), registry=schema_registry)
            for key in ("academic-chat-response", "transcript-assessment-response", "transcript-followup-response")
        }

    def setUp(self):
        self.engine = AnswerEngine(self.registry)
        self.facade = GroundedChatEngine(self.engine, NoInference())
        self.engine_patch = patch.object(api, "_engine", return_value=self.engine)
        self.chat_patch = patch.object(api, "_chat_engine", return_value=self.facade)
        self.engine_patch.start()
        self.chat_patch.start()
        self.addCleanup(self.chat_patch.stop)
        self.addCleanup(self.engine_patch.stop)
        # No lifespan means the patched factories need not pretend to be caches.
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def post(self, path, value, schema=None):
        response = self.client.post(path, json=value)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        result = response.json()
        if schema:
            self.schemas[schema].validate(result)
        return result

    def assert_packet(self, packet):
        self.assertEqual([], validate_evidence_packet(ROOT, packet))
        if packet["status"] == "supported":
            self.assertTrue(packet["applied_rules"])
            self.assertTrue(packet["evidence"])
            for applied in packet["applied_rules"]:
                rule = self.registry.rules[applied["rule_id"]]
                self.assertEqual(self.registry.rule_hashes[applied["rule_id"]], applied["rule_sha256"])
                self.assertEqual("human", rule["review"]["mode"])
                self.assertEqual("full", rule["review"]["scope"])
                self.assertEqual("approved", rule["review"]["status"])
                self.assertTrue(any(cite["rule_id"] == applied["rule_id"] and cite["claim"] == rule["decision"]["statement"]
                                    for cite in packet["evidence"]))
        else:
            self.assertEqual([], packet["applied_rules"])
            self.assertEqual([], packet["evidence"])
            self.assertTrue(packet["issues"])

    def test_chat_replays_current_facts_and_does_not_retain_prior_client_state(self):
        packet_ids = set()
        for earned, expected_gap in ((113, 17), (129, 1), (143, 0)):
            result = self.post("/v1/academic/chat", chat_payload(
                "그럼 몇 학점 남았어?", previous_question="졸업에 필요한 총학점",
                earned_credits={"credits.graduation.total": earned}), "academic-chat-response")
            self.assertEqual("supported", result["status"])
            self.assertTrue(result["context_used"])
            self.assertEqual({"credits.graduation.total": earned}, result["evidence_packet"]["student_facts"])
            self.assertEqual((earned, expected_gap), (result["calculations"][0]["earned"], result["calculations"][0]["gap"]))
            self.assert_packet(result["evidence_packet"])
            packet_ids.add(result["packet_id"])
        self.assertEqual(3, len(packet_ids))
        result = self.post("/v1/academic/chat", chat_payload("그럼 몇 학점 남았어?"), "academic-chat-response")
        self.assertEqual("insufficient_evidence", result["status"])
        self.assertFalse(result["context_used"])
        self.assertEqual([], result["calculations"])
        self.assert_packet(result["evidence_packet"])

    def test_chat_scope_and_unapproved_prior_are_checked_again_at_api(self):
        for previous, updates, status in (
            ("25학번 졸업학점", {}, "out_of_scope"),
            ("전자공학과 졸업학점", {}, "out_of_scope"),
            ("졸업학점", {"matched_curriculum_year": 2025}, "out_of_scope"),
            ("졸업학점", {"department": "간호학과"}, "out_of_scope"),
            ("현재 PCCP 합격 기준", {}, "insufficient_evidence"),
            ("졸논 공모전 면제", {}, "insufficient_evidence"),
            ("교양 말고 전공 기준", {}, "insufficient_evidence"),
        ):
            with self.subTest(previous=previous, updates=updates):
                result = self.post("/v1/academic/chat", chat_payload("그건 다시 설명해줘", previous_question=previous, **updates))
                self.assertEqual(status, result["status"])
                self.assertFalse(result["context_used"])
                self.assertEqual([], result["calculations"])
                self.assert_packet(result["evidence_packet"])

    def test_prior_conflict_cannot_turn_into_supported_api_context(self):
        conflict = replace(self.registry, conflicts={"credit_threshold:credits.graduation.total": ("cwnu.cs.2026.credits.graduation-total",)})
        with patch.object(api, "_chat_engine", return_value=GroundedChatEngine(AnswerEngine(conflict), NoInference())):
            result = self.post("/v1/academic/chat", chat_payload("그럼 몇 학점인가요?", previous_question="졸업학점"))
        self.assertEqual("conflict", result["status"])
        self.assertFalse(result["context_used"])
        self.assertEqual([], result["clarification_choices"])
        self.assert_packet(result["evidence_packet"])

    def test_new_aliases_cannot_answer_only_part_of_an_unverified_question(self):
        for question in (
            "졸논 필수야 등록 마감은 언제야",
            "졸논 등록 마감은 언제야",
            "졸업에 필요한 총학점 등록 마감은 언제야",
            "전필은 몇 과목이야 등록 마감도 알려줘",
        ):
            with self.subTest(question=question):
                result = self.post("/v1/academic/chat", chat_payload(question))
                self.assertEqual("insufficient_evidence", result["status"])
                self.assertFalse(result["context_used"])
                self.assert_packet(result["evidence_packet"])

    def test_synthetic_identifier_and_client_authored_state_fail_without_echo(self):
        for value in (
            chat_payload("그럼 몇 학점인가요?", previous_question="학번 2026123456 졸업학점"),
            chat_payload("그럼 몇 학점인가요?", previous_question="student@example.invalid 졸업학점"),
            chat_payload("그럼 몇 학점인가요?", previous_question={"status": "supported"}),
            chat_payload("그럼 몇 학점인가요?", history=["졸업학점"]),
            chat_payload("그럼 몇 학점인가요?", previous_rule_ids=["invented"]),
        ):
            response = self.client.post("/v1/academic/chat", json=value)
            self.assertEqual(422, response.status_code)
            self.assertEqual({"detail": "invalid request"}, response.json())
            self.assertEqual("no-store", response.headers["cache-control"])

    def test_unrelated_current_credits_cannot_be_replayed_as_prior_metric(self):
        result = self.post("/v1/academic/chat", chat_payload(
            "그럼 몇 학점인가요?", previous_question="졸업학점",
            earned_credits={"credits.general.total": 30}))
        self.assertEqual("insufficient_evidence", result["status"])
        self.assertFalse(result["context_used"])
        self.assertEqual([], result["calculations"])
        self.assert_packet(result["evidence_packet"])

    def test_graph_failure_remains_sanitized_503_at_transcript_and_chat_boundaries(self):
        with patch.object(api, "_engine", side_effect=RegistryUnavailable()), patch.object(api, "_chat_engine", side_effect=RegistryUnavailable()):
            for path, value in (
                ("/v1/academic/chat", chat_payload("졸업학점")),
                ("/v1/academic/transcripts/assess", transcript()),
                ("/v1/academic/transcripts/chat", {"question": "남은 학점 알려줘", "transcript": transcript()}),
            ):
                response = self.client.post(path, json=value)
                self.assertEqual(503, response.status_code)
                self.assertEqual({"detail": "service unavailable"}, response.json())
                self.assertNotIn("synthetic-private-detail", response.text)

    def test_transcript_unmatched_mixed_negative_exception_and_scope_questions_do_not_select_checks(self):
        for question, status in (
            ("2026학번 전자공학과 남은 학점 알려줘", "out_of_scope"),
            ("25학번 남은 학점 알려줘", "out_of_scope"),
            ("교양 몇 학점 남았어 그리고 논문 완료했어", "insufficient_evidence"),
            ("전공 아닌 교양 학점 부족분 알려줘", "insufficient_evidence"),
            ("교양 또는 전공 학점 부족분 알려줘", "insufficient_evidence"),
            ("편입 경과조치로 남은 학점 알려줘", "insufficient_evidence"),
            ("졸업논문 면제됐으니 남은 학점 알려줘", "insufficient_evidence"),
            ("남은 학점 알려줘 등록 마감은 언제야", "insufficient_evidence"),
        ):
            with self.subTest(question=question):
                result = self.post("/v1/academic/transcripts/chat", {"question": question, "transcript": transcript()}, "transcript-followup-response")
                self.assertEqual(status, result["status"])
                self.assertEqual([], result["selected_checks"])
                self.assertEqual([], result["focus_check_ids"])
                self.assertEqual([], result["verification_items"])

    def test_transcript_supported_focus_has_approved_claims_and_current_facts(self):
        for credits in (3, 7):
            result = self.post("/v1/academic/transcripts/chat", {
                "question": "2026학번 컴퓨터공학과 졸업까지 몇 학점 더 필요해?",
                "transcript": transcript([row("synthetic-1", credits=credits)])}, "transcript-followup-response")
            self.assertEqual("supported", result["status"])
            self.assertEqual(["credits.graduation.total"], result["focus_check_ids"])
            check = result["selected_checks"][0]
            self.assertEqual(130 - credits, check["gap"])
            self.assertEqual({"credits.graduation.total": credits}, check["evidence_packet"]["student_facts"])
            self.assert_packet(check["evidence_packet"])

    def test_incomplete_transcript_never_invents_missing_courses_or_gap_evidence(self):
        result = self.post("/v1/academic/transcripts/assess", transcript(record_complete=False), "transcript-assessment-response")
        self.assertEqual("insufficient_evidence", result["status"])
        self.assertFalse(result["official_graduation_decision"])
        for check in result["checks"]:
            self.assert_packet(check["evidence_packet"])
            if check["result"] == "needs_review":
                self.assertIsNone(check["gap"])
                self.assertEqual([], check["missing_courses"])
                self.assertEqual("insufficient_evidence", check["evidence_packet"]["status"])
                self.assert_packet(check["policy_packet"])
                self.assertEqual({}, check["policy_packet"]["student_facts"])
        incomplete = next(item for item in result["verification_items"] if item["kind"] == "record_completeness")
        self.assertEqual([], incomplete["row_ids"])

    def test_overlap_duplicate_failed_excluded_and_zero_credit_flags_hold_each_pass_row_once(self):
        courses = [
            row("synthetic-1", "고급자료구조", course_code="CDA0143", category="major_required",
                review_flags=["retake", "equivalence", "retroactivity", "recognition_unverified"]),
            row("synthetic-2", "고급자료구조", course_code="CDA0143", category="major_required", grade="F"),
            row("synthetic-3", "합성 미확인", credits=7, category="unknown", review_flags=["equivalence"]),
            row("synthetic-4", "합성 제외", credits=11, excluded=True, review_flags=["retake"]),
            row("synthetic-5", "졸업논문", credits=0, grade="S", course_code="CDA0034",
                category="major_required", review_flags=["equivalence"]),
            row("synthetic-6", "합성 확정", credits=5),
        ]
        result = self.post("/v1/academic/transcripts/assess", transcript(courses), "transcript-assessment-response")
        self.assertEqual(15, result["raw_earned_credits"])
        self.assertEqual({"input_pass_credits": 15, "conditional_graduation_credits": None,
                          "unresolved_pass_credits": 10, "recognition_status": "needs_review"}, result["credit_summary"])
        self.assertIsNone(result["recognized_graduation_credits"])
        item_ids = [item["item_id"] for item in result["verification_items"]]
        self.assertEqual(len(item_ids), len(set(item_ids)))
        for item in result["verification_items"]:
            self.assertNotIn("synthetic-4", item["row_ids"])
            self.assertTrue(item["action"])
        for check in result["checks"]:
            self.assertEqual("needs_review", check["result"])
            self.assert_packet(check["evidence_packet"])
            self.assert_packet(check["policy_packet"])

    def test_credit_summary_applies_general_cap_and_excludes_nonpass_or_excluded_rows(self):
        courses = [row("synthetic-1", "합성 기초", credits=20, category="foundation"),
                   row("synthetic-2", "합성 균형", credits=25, category="balanced"),
                   row("synthetic-3", "합성 확장", credits=10, category="expanded"),
                   row("synthetic-4", "합성 자유", credits=7),
                   row("synthetic-5", "합성 실패", credits=12, grade="U"),
                   row("synthetic-6", "합성 제외", credits=30, excluded=True)]
        result = self.post("/v1/academic/transcripts/assess", transcript(courses), "transcript-assessment-response")
        self.assertEqual(62, result["raw_earned_credits"])
        self.assertEqual(49, result["recognized_graduation_credits"])
        self.assertEqual(49, result["credit_summary"]["conditional_graduation_credits"])
        self.assertEqual(0, result["credit_summary"]["unresolved_pass_credits"])
        self.assertFalse(result["official_graduation_decision"])
        for check in result["checks"]:
            self.assert_packet(check["evidence_packet"])
            if check["policy_packet"]:
                self.assert_packet(check["policy_packet"])

    def test_transcript_verification_only_returns_row_actions_without_personal_approval(self):
        result = self.post("/v1/academic/transcripts/chat", {"question": "재수강 확인이 필요한 과목 알려줘",
            "transcript": transcript([row("synthetic-1", review_flags=["retake", "equivalence"])])}, "transcript-followup-response")
        self.assertEqual("insufficient_evidence", result["status"])
        self.assertEqual(["duplicate_or_retake"], [item["kind"] for item in result["verification_items"]])
        self.assertEqual(["synthetic-1"], result["verification_items"][0]["row_ids"])
        self.assertTrue(result["focus_check_ids"])
        for check in result["selected_checks"]:
            self.assert_packet(check["evidence_packet"])
            self.assertEqual("insufficient_evidence", check["evidence_packet"]["status"])
            self.assert_packet(check["policy_packet"])
            self.assertEqual({}, check["policy_packet"]["student_facts"])

    def test_invalid_flags_duplicate_row_ids_and_unconfirmed_payloads_are_sanitized(self):
        for value in (
            transcript([row("synthetic-1", review_flags=["approved"])]),
            transcript([row("synthetic-1"), row("synthetic-1", "합성 다른 과목")]),
            transcript(confirmed=1), transcript(confirmed=False),
            transcript([row("synthetic-1", course_name="학번 2026123456")]),
        ):
            response = self.client.post("/v1/academic/transcripts/assess", json=value)
            self.assertEqual(422, response.status_code)
            self.assertEqual({"detail": "invalid request"}, response.json())


class IndependentSupervisorQA(unittest.TestCase):
    def test_configuration_failures_are_sanitized_before_any_spawn_or_bind(self):
        base = {"ACADEMIC_EVIDENCE_BACKEND": "neo4j", "NEO4J_URI": "bolt://127.0.0.1:7687",
                "NEO4J_DATABASE": "neo4j", "NEO4J_USER": "synthetic", "NEO4J_PASSWORD": "synthetic-secret",
                "ACADEMIC_LLM_PROVIDER": "disabled"}
        variants = [dict(base, NEO4J_URI="bolt://203.0.113.10:7687"), dict(base, NEO4J_PASSWORD=""),
                    dict(base, ACADEMIC_EVIDENCE_BACKEND="registry"),
                    dict(base, ACADEMIC_NEO4J_TIMEOUT_SECONDS="nan"), dict(base, ACADEMIC_LLM_PROVIDER="unknown")]
        for config in variants:
            output = io.StringIO()
            with self.subTest(config=config), patch.object(supervisor, "load_private_settings", return_value=config), \
                    patch.object(supervisor, "spawn_web") as spawn, patch.object(supervisor, "port_available") as bind, redirect_stdout(output):
                self.assertEqual(supervisor.EXIT_CONFIG, supervisor.main([]))
            self.assertEqual({"event": "configuration_unavailable"}, json.loads(output.getvalue()))
            spawn.assert_not_called()
            bind.assert_not_called()

    def test_ephemeral_diagnostics_require_exact_ready_and_verified_graph_and_recover(self):
        with diagnostic_server() as (responses, received):
            cases = [
                ((200, b'{"status":"ready"}'), (200, b'{"evidence_backend":"registry","graph_verified":true}'), "unavailable"),
                ((200, b'{"status":"ready"}'), (200, b'{"evidence_backend":"neo4j","graph_verified":1}'), "unavailable"),
                ((503, b'{"secret":"synthetic-secret"}'), (503, b'{"secret":"synthetic-secret"}'), "unavailable"),
                ((200, b'{"status":"ready"}'), (200, b'{"evidence_backend":"neo4j","graph_verified":true,"llm_configured":true,"llm_model_available":false}'), "ready"),
            ]
            for ready, runtime, expected in cases:
                responses["/readyz"] = ready
                responses["/v1/academic/runtime"] = runtime
                output = io.StringIO()
                with redirect_stdout(output), patch.object(supervisor, "spawn_web") as spawn, patch.object(supervisor, "private_environment") as config:
                    code = supervisor.main(["--check-once"])
                event = json.loads(output.getvalue())
                self.assertEqual(expected, event["readiness"])
                self.assertEqual(supervisor.EXIT_OK if expected == "ready" else supervisor.EXIT_NOT_READY, code)
                self.assertNotIn("synthetic-secret", output.getvalue())
                spawn.assert_not_called()
                config.assert_not_called()
            self.assertEqual([("GET", "/readyz"), ("GET", "/v1/academic/runtime")] * 4, received)

    def test_slow_diagnostic_body_cannot_exceed_supervisor_runtime_budget(self):
        child = Mock()
        child.process.pid = 12345
        child.process.poll.return_value = None
        child.stop.return_value = True
        limits = supervisor.Limits(max_restarts=0, backoff_seconds=0.1, max_backoff_seconds=0.1,
            poll_seconds=0.1, startup_timeout_seconds=1, shutdown_timeout_seconds=0.1, max_runtime_seconds=0.2)
        with diagnostic_server(slow=True), patch.object(supervisor, "port_available", return_value=True), \
                patch.object(supervisor, "spawn_web", return_value=child), patch.object(supervisor, "web_listening", return_value=True), redirect_stdout(io.StringIO()):
            start = time.monotonic()
            code = supervisor.supervise({}, limits, threading.Event())
            elapsed = time.monotonic() - start
        child.stop.assert_called_once_with(0.1)
        self.assertEqual(supervisor.EXIT_OK, code)
        self.assertLess(elapsed, 0.65, f"Slow body escaped the 0.2s runtime bound: {elapsed:.3f}s")

    def test_ambiguous_oversized_and_incomplete_http_framing_closes_owned_connections(self):
        body = b'{"status":"ready"}'
        start = b"HTTP/1.1 200 OK\r\n"
        length = f"Content-Length: {len(body)}\r\n".encode()
        cases = [
            ("duplicate-length", start + length + length + b"\r\n" + body, False),
            ("mixed-transfer-framing", start + length + b"Transfer-Encoding: chunked\r\n\r\n" + body, False),
            ("short-body", start + f"Content-Length: {len(body)+3}\r\n\r\n".encode() + body, False),
            ("excess-body", start + f"Content-Length: {len(body)-3}\r\n\r\n".encode() + body, False),
            ("keep-open-without-length", start + b"\r\n" + body, True),
            ("oversized-header", start + b"X-Synthetic: " + b"a" * 8192 + b"\r\n" + length + b"\r\n" + body, False),
            ("oversized-body", start + b"Content-Length: 8193\r\n\r\n" + b"a" * 8193, False),
        ]
        actual_socket = socket.socket
        for name, raw, keep_open in cases:
            connections = []

            def owned_socket(*args, **kwargs):
                connection = actual_socket(*args, **kwargs)
                if "fileno" not in kwargs:
                    connections.append(connection)
                return connection

            with self.subTest(case=name), raw_diagnostic_server(raw, keep_open=keep_open), \
                    patch.object(supervisor.socket, "socket", side_effect=owned_socket):
                started = time.monotonic()
                _code, data = supervisor._get_json("/readyz", 0.1)
                elapsed = time.monotonic() - started
                self.assertIsNone(data)
                self.assertLess(elapsed, 0.35)
                self.assertEqual(1, len(connections))
                self.assertEqual(-1, connections[0].fileno())

    def test_deep_json_cannot_escape_diagnostics_or_abort_live_supervision(self):
        malformed = b'{"x":' + b"[" * 4000 + b"0" + b"]" * 4000 + b"}"
        self.assertLessEqual(len(malformed), supervisor.MAX_RESPONSE_BYTES)
        child = Mock()
        child.process.pid = 12345
        child.process.poll.return_value = None
        child.stop.return_value = True
        limits = supervisor.Limits(max_restarts=0, poll_seconds=0.1,
            shutdown_timeout_seconds=0.1, max_runtime_seconds=0.2)
        with diagnostic_server() as (responses, _requests):
            responses["/readyz"] = (200, malformed)
            with self.subTest(boundary="diagnostic-parser"):
                self.assertEqual((None, None), supervisor._get_json("/readyz", 0.1))
            with self.subTest(boundary="supervisor"), patch.object(supervisor, "port_available", return_value=True), \
                    patch.object(supervisor, "spawn_web", return_value=child), patch.object(supervisor, "web_listening", return_value=True), redirect_stdout(io.StringIO()):
                self.assertEqual(supervisor.EXIT_OK, supervisor.supervise({}, limits, threading.Event()))
        child.stop.assert_called_once_with(0.1)

    def test_root_inclusive_depth_64_accepts_and_65_rejects_before_decoder(self):
        original_decoder = json.loads
        with diagnostic_server() as (responses, _requests):
            for kind in ("arrays", "objects", "mixed"):
                for depth in (64, 65):
                    opening = [b"[" if kind == "arrays" or (kind == "mixed" and index % 2) else b'{"child":'
                               for index in range(depth - 1)]
                    closing = [b"]" if token == b"[" else b"}" for token in opening]
                    body = b'{"chain":' + b"".join(opening) + b"0" + b"".join(reversed(closing)) + b"}"
                    responses["/readyz"] = (200, body)
                    with self.subTest(kind=kind, depth=depth):
                        if depth == 65:
                            with patch.object(supervisor.json, "loads", side_effect=AssertionError("over-depth response reached decoder")) as decoder:
                                self.assertEqual((None, None), supervisor._get_json("/readyz", 0.2))
                                decoder.assert_not_called()
                        else:
                            with patch.object(supervisor.json, "loads", wraps=original_decoder) as decoder:
                                code, data = supervisor._get_json("/readyz", 0.2)
                                decoder.assert_called_once()
                            self.assertEqual(200, code)
                            value = data["chain"]
                            for token in opening:
                                value = value[0] if token == b"[" else value["child"]
                            self.assertEqual(0, value)

    def test_quoted_escaped_container_text_and_wide_siblings_do_not_consume_depth(self):
        marker = '합성 🎓 [ { "quoted" } ] ' * 40 + 'backslash \\ escaped quote " brackets [{}]'
        leaf = json.dumps(marker, ensure_ascii=False).encode("utf-8")
        at_limit = b'{"chain":' + b"[" * 63 + leaf + b"]" * 63 + b"}"
        escaped_unicode = b'{"note":"\\u005b\\u007b\\u0022\\u005c\\u0022\\u007d\\u005d"}'
        wide = json.dumps({"siblings": [[] for _ in range(100)], "note": marker}, ensure_ascii=False).encode("utf-8")
        with diagnostic_server() as (responses, _requests):
            for name, body in (("depth-64-string", at_limit), ("unicode-escapes", escaped_unicode), ("wide-siblings", wide)):
                responses["/readyz"] = (200, body)
                with self.subTest(case=name):
                    code, data = supervisor._get_json("/readyz", 0.2)
                    self.assertEqual(200, code)
                    if name == "depth-64-string":
                        value = data["chain"]
                        for _ in range(63):
                            value = value[0]
                        self.assertEqual(marker, value)
                    elif name == "unicode-escapes":
                        self.assertEqual('[{"\\"}]', data["note"])
                    else:
                        self.assertEqual(100, len(data["siblings"]))
                        self.assertEqual(marker, data["note"])

    def test_alternate_encodings_and_invalid_utf8_cannot_bypass_depth_contract(self):
        ready = '{"status":"ready"}'
        over_depth = '{"chain":' + "[" * 64 + "0" + "]" * 64 + "}"
        bodies = [(encoding, text.encode(encoding))
                  for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be")
                  for text in (ready, over_depth)]
        bodies.extend(("invalid-utf8", body) for body in (
            b'{"note":"\xff"}', b'{"note":"\xc0\xaf"}', b'{"note":"\xed\xa0\x80"}'))
        with diagnostic_server() as (responses, _requests):
            for index, (encoding, body) in enumerate(bodies):
                responses["/readyz"] = (200, body)
                with self.subTest(encoding=encoding, index=index):
                    self.assertEqual((None, None), supervisor._get_json("/readyz", 0.2))

    def test_malformed_strings_escapes_and_container_types_remain_unavailable(self):
        bodies = (b'{"chain":[0}', b'{"chain":{0]}', b'{"note":"unterminated}',
                  b'{"note":"bad\\q"}', b'{"note":"bad\\uZZZZ"}', b'{"note":"ends-with-backslash\\"}')
        with diagnostic_server() as (responses, _requests):
            for index, body in enumerate(bodies):
                responses["/readyz"] = (200, body)
                with self.subTest(case=index):
                    self.assertEqual((None, None), supervisor._get_json("/readyz", 0.2))


if __name__ == "__main__":
    unittest.main()
