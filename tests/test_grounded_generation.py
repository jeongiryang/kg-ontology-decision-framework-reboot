from __future__ import annotations

import copy
import io
import json
import socket
import sys
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant import llm
from academic_assistant.core import AnswerEngine
from academic_assistant.grounded_chat import GroundedChatEngine, GroundedChatResponse
from academic_assistant.grounded_generation import (
    build_public_plan, generate_grounded, grounded_generation_enabled,
    permitted_sentences, verify_document,
)
from academic_assistant.models import AcademicAnswerRequest, AcademicChatRequest
from academic_assistant.registry import Registry, canonical_sha256


def request(question="졸업 총학점 기준 알려줘", **updates):
    return AcademicChatRequest(question=question, admission_year=2026,
                               matched_curriculum_year=2026, department="컴퓨터공학과", **updates)


def document(plan, *, alternate=False):
    return {"basis_sha256": plan["basis_sha256"], "introduction": "확인된 기준을 안내해 드리겠습니다.",
            "sentences": [{"claim_id": claim["claim_id"],
                           "text": permitted_sentences(claim)[-1] if alternate else claim["statement"]}
                          for claim in plan["claims"]]}


class FakeOpener:
    def __init__(self, response):
        self.response, self.calls = response, []

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return io.BytesIO(self.response)


class GroundedGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load(ROOT)
        cls.engine = AnswerEngine(cls.registry)

    def setUp(self):
        self.budget = patch.object(llm, "_budget", llm._RequestBudget())
        self.budget.start(); self.addCleanup(self.budget.stop)
        self.now = 100.0
        self.clock = patch.object(llm.time, "monotonic", side_effect=lambda: self.now)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.base = self.engine.answer(request())
        self.plan = build_public_plan(self.base, self.registry)

    def client(self, doc=None, raw=None, **settings):
        client = llm.LocalLLMClient(llm.LLMSettings("ollama", "http://127.0.0.1:11434", "gemma-test",
                                                   min_interval_seconds=60, **settings))
        if raw is None:
            raw = json.dumps({"response": json.dumps(doc or document(self.plan), ensure_ascii=False)}, ensure_ascii=False).encode()
        client._opener = FakeOpener(raw)
        # Generation has its own absolute-deadline socket transport. This seam
        # preserves all existing request capture/privacy/bytes assertions.
        client._post_grounded = lambda req: client._opener.open(req, timeout=client.settings.timeout_seconds).read(client.settings.max_response_bytes + 1)
        return client

    def assert_fallback(self, outcome, status):
        self.assertEqual(status, outcome.generation_status)
        self.assertIsNone(outcome.generated_answer)
        self.assertEqual((), outcome.generated_claim_ids)

    def test_real_model_text_survives_without_reconstruction(self):
        doc = document(self.plan, alternate=True)
        outcome = generate_grounded(self.base, self.registry, self.client(doc))
        self.assertEqual("generated", outcome.generation_status)
        self.assertEqual("확인된 기준을 안내해 드리겠습니다.\n졸업 총학점 기준은 130학점 이상입니다.", outcome.generated_answer)
        self.assertEqual(tuple(c["claim_id"] for c in self.plan["claims"]), outcome.generated_claim_ids)
        self.assertNotEqual(self.base.answer, outcome.generated_answer)

    def test_closed_credit_grammar_and_whole_statement_prefix(self):
        for text in permitted_sentences(self.plan["claims"][0]):
            doc = document(self.plan); doc["sentences"][0]["text"] = text
            self.assertEqual(text, verify_document(self.plan, doc).answer.split("\n")[-1])

    def test_changed_numbers_negation_sufficiency_and_extra_sentences_rejected(self):
        for text in ("졸업 총학점 기준은 131학점 이상입니다.", "졸업 총학점 기준은 130학점 이하입니다.",
                     "130학점이면 졸업할 수 있습니다.", "졸업 총학점은 최소 130학점을 이수하지 않아도 됩니다.",
                     "졸업 총학점 기준은 130학점 이상입니다. 추가 조건은 없습니다.",
                     " 졸업 총학점 기준은 130학점 이상입니다."):
            doc = document(self.plan); doc["sentences"][0]["text"] = text
            with self.subTest(text=text), self.assertRaises(llm.LLMInvalidResponse):
                verify_document(self.plan, doc)

    def test_korean_particle_must_agree_with_fixed_subject(self):
        for question, particle in (("졸업 총학점 기준", "은"), ("교양 총학점 기준", "은"),
                                   ("전공필수 학점 기준", "는"), ("전공선택 학점 기준", "은")):
            plan = build_public_plan(self.engine.answer(request(question)), self.registry)
            claim = plan["claims"][0]
            correct = f"{claim['subject']}{particle} 최소 {claim['credits']}학점을 이수해야 합니다."
            doc = document(plan); doc["sentences"][0]["text"] = correct
            self.assertIn(correct, verify_document(plan, doc).answer)
            wrong = "는" if particle == "은" else "은"
            doc["sentences"][0]["text"] = f"{claim['subject']}{wrong} 최소 {claim['credits']}학점을 이수해야 합니다."
            with self.assertRaises(llm.LLMInvalidResponse): verify_document(plan, doc)

    def test_missing_duplicate_reordered_unknown_claims_and_basis_rejected(self):
        base = self.engine.answer(request("전공학점 기준 알려줘"))
        plan = build_public_plan(base, self.registry)
        self.assertEqual(5, len(plan["claims"]))
        original = document(plan)
        mutations = []
        doc = copy.deepcopy(original); doc["sentences"].pop(); mutations.append(doc)
        doc = copy.deepcopy(original); doc["sentences"].append(doc["sentences"][0]); mutations.append(doc)
        doc = copy.deepcopy(original); doc["sentences"][1] = doc["sentences"][0]; mutations.append(doc)
        doc = copy.deepcopy(original); doc["sentences"].reverse(); mutations.append(doc)
        doc = copy.deepcopy(original); doc["sentences"][0]["claim_id"] = "unknown"; mutations.append(doc)
        doc = copy.deepcopy(original); doc["basis_sha256"] = "0" * 64; mutations.append(doc)
        doc = copy.deepcopy(original); doc["sentences"][0]["extra"] = "x"; mutations.append(doc)
        doc = copy.deepcopy(original); doc["introduction"] = "졸업이 가능합니다."; mutations.append(doc)
        for doc in mutations:
            with self.subTest(doc=doc), self.assertRaises(llm.LLMInvalidResponse): verify_document(plan, doc)

    def test_operational_caveats_and_none_listed_must_survive(self):
        for question in ("현재 PCCP 합격 기준 알려줘", "졸업논문 대체요건 알려줘"):
            base = self.engine.answer(request(question)); self.assertEqual("supported", base.status)
            plan = build_public_plan(base, self.registry)
            doc = document(plan)
            self.assertIn(plan["claims"][0]["statement"], verify_document(plan, doc).answer)
            doc["sentences"][0]["text"] = ("PCCP 합격 기준은 400점 이상이다." if "PCCP" in question
                                             else "졸업논문 대체는 금지된다.")
            with self.assertRaises(llm.LLMInvalidResponse): verify_document(plan, doc)
            self.assertNotIn("metric", plan["claims"][0])

    def test_complete_private_course_labels_are_public_plan_not_student_records(self):
        base = self.engine.answer(request("전공필수 과목 목록 알려줘"))
        plan = build_public_plan(base, self.registry)
        self.assertIn("지정 과목은 고급자료구조, 컴퓨터구조, 알고리즘, 소프트웨어공학, 데이터베이스개론, 운영체제, 데이터통신, 졸업논문, 심층상담이다.", plan["claims"][0]["statement"])
        doc = document(plan); verify_document(plan, doc)
        doc["sentences"][0]["text"] = doc["sentences"][0]["text"].replace(", 심층상담", "")
        with self.assertRaises(llm.LLMInvalidResponse): verify_document(plan, doc)

    def test_rule_hash_approval_scope_and_packet_basis_independently_checked(self):
        rule_id = self.plan["claims"][0]["claim_id"]
        for mutation in ("hash", "approval", "scope", "record_only", "rule_id"):
            registry, base = copy.deepcopy(self.registry), self.base.model_copy(deep=True)
            rule = registry.rules[rule_id]
            if mutation == "hash": rule["decision"]["statement"] += " 변경"
            elif mutation == "approval": rule["review"]["status"] = "pending"
            elif mutation == "scope": rule["applicability"]["admission_years"] = [2025]
            elif mutation == "record_only": rule["answer_policy"] = "record_only"
            else: rule["rule_id"] = "different"
            if mutation != "hash":
                registry.rule_hashes[rule_id] = canonical_sha256(rule)
                base.evidence_packet.applied_rules[0].rule_sha256 = registry.rule_hashes[rule_id]
            with self.subTest(mutation=mutation): self.assert_fallback(generate_grounded(base, registry, self.client()), "rejected")
        base = self.base.model_copy(deep=True); base.evidence_packet.scope.admission_year = 2025
        self.assert_fallback(generate_grounded(base, self.registry, self.client()), "rejected")
        base = self.base.model_copy(deep=True); base.evidence_packet.applied_rules[0].rule_sha256 = "0" * 64
        self.assert_fallback(generate_grounded(base, self.registry, self.client()), "rejected")

    def test_registry_mutation_during_call_rejected_at_consumer(self):
        registry = copy.deepcopy(self.registry)
        client = self.client()
        def mutate(plan):
            result = verify_document(plan, document(plan))
            registry.rules[plan["claims"][0]["claim_id"]]["review"]["status"] = "pending"
            return result
        with patch.object(client, "generate_grounded", side_effect=mutate):
            self.assert_fallback(generate_grounded(self.base, registry, client), "rejected")

    def test_forged_consumer_answer_and_coverage_rejected(self):
        original = verify_document(self.plan, document(self.plan))
        for result in (replace(original, answer="학사 사실을 발명했습니다."),
                       replace(original, claim_ids=()), replace(original, cached="yes")):
            client = self.client()
            with patch.object(client, "generate_grounded", return_value=result):
                self.assert_fallback(generate_grounded(self.base, self.registry, client), "rejected")

    def test_invalid_json_utf8_duplicate_properties_and_bytes_bound(self):
        for raw in (b"not json", b"\xff", b'{"response":"{}","response":"{}"}',
                    b'{"response":"not json"}', b"[]", b"x" * 8193):
            client = self.client(raw=raw)
            with self.subTest(raw=raw[:30]), self.assertRaises(llm.LLMInvalidResponse): client.generate_grounded(self.plan)
            self.now += 60
            self.assertEqual(0, len(llm._budget.generation_cache))

    def test_transport_request_is_bounded_non_streaming_schema_only(self):
        client = self.client(timeout_seconds=20, max_response_bytes=32768)
        client.generate_grounded(self.plan)
        req, timeout = client._opener.calls[0]
        payload = json.loads(req.data)
        self.assertEqual("http://127.0.0.1:11434/api/generate", req.full_url)
        self.assertEqual(20, timeout); self.assertFalse(payload["stream"]); self.assertFalse(payload["think"])
        self.assertEqual({"temperature": 0, "num_predict": 512}, payload["options"])
        self.assertEqual("60s", payload["keep_alive"])
        self.assertFalse(payload["format"]["additionalProperties"])
        self.assertEqual(1, payload["format"]["properties"]["sentences"]["maxItems"])
        self.assertNotIn("enum", payload["format"]["properties"]["sentences"]["items"]["properties"]["text"])
        task = json.loads(payload["prompt"])
        self.assertIn("Generate Korean guidance", task["instruction"])
        self.assertIn("credit_grammar", task)
        self.assertNotIn("permitted_texts", task)

    def test_no_student_question_previous_answer_or_calculations_in_prompt_cache(self):
        req = request(earned_credits={"credits.graduation.total": 127}, previous_question="교양 학점 기준 알려줘")
        base = self.engine.answer(req); plan = build_public_plan(base, self.registry)
        self.assertEqual(self.plan, plan)
        client = self.client(document(plan)); generate_grounded(base, self.registry, client)
        payload = client._opener.calls[0][0].data.decode()
        cached = repr(llm._budget.generation_cache)
        for forbidden in (req.question, req.previous_question, base.packet_id, "student_facts", "earned", '"gap"', "127학점"):
            self.assertNotIn(forbidden, payload); self.assertNotIn(forbidden, cached)
        other = self.engine.answer(request(earned_credits={"credits.graduation.total": 19}))
        self.assertEqual("cached", generate_grounded(other, self.registry, client).generation_status)
        self.assertEqual(1, len(client._opener.calls))

    def test_private_fields_are_rejected_before_cache_or_transport(self):
        client = self.client()
        for key in ("question", "student_facts", "answer", "packet_id"):
            plan = copy.deepcopy(self.plan); plan[key] = "private"
            with self.assertRaises(llm.LLMInvalidResponse): client.generate_grounded(plan)
        self.assertEqual([], client._opener.calls); self.assertEqual(0, len(llm._budget.generation_cache))

    def test_cache_hit_is_cached_and_revalidated(self):
        client = self.client()
        self.assertFalse(client.generate_grounded(self.plan).cached)
        other = self.client()
        self.assertTrue(other.generate_grounded(self.plan).cached)
        self.assertEqual([], other._opener.calls)
        key = next(iter(llm._budget.generation_cache))
        llm._budget.generation_cache[key] = (self.now, '{"corrupted":"cache"}')
        with self.assertRaises(llm.LLMInvalidResponse): other.generate_grounded(self.plan)

    def test_cache_ttl_300_and_settings_separation(self):
        client = self.client(); client.generate_grounded(self.plan)
        self.now += 299.999
        self.assertTrue(client.generate_grounded(self.plan).cached)
        self.now += .001
        self.assertFalse(client.generate_grounded(self.plan).cached)
        self.assertEqual(2, len(client._opener.calls))
        self.now += 60
        other = self.client(timeout_seconds=20)
        self.assertFalse(other.generate_grounded(self.plan).cached)

    def test_cache_max_32_distinct_public_plans(self):
        client = self.client()
        with patch.object(client, "_request_grounded", side_effect=lambda plan: document(plan)):
            for index in range(40):
                plan = copy.deepcopy(self.plan)
                plan["claims"][0]["rule_sha256"] = f"{index:064x}"
                plan["basis_sha256"] = canonical_sha256({k: v for k, v in plan.items() if k != "basis_sha256"})
                client.generate_grounded(plan); self.now += 60
        self.assertEqual(32, len(llm._budget.generation_cache))

    def test_generation_and_intent_share_active_and_interval_budget(self):
        client = self.client()
        entered, release = threading.Event(), threading.Event()
        def slow(plan): entered.set(); release.wait(2); return document(plan)
        with patch.object(client, "_request_grounded", side_effect=slow), patch.object(client, "_request_intent") as intent:
            worker = threading.Thread(target=client.generate_grounded, args=(self.plan,)); worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertIsNone(client.suggest_intent("credits", {"course.one": "x"}))
                with self.assertRaises(llm.LLMBusy): client.generate_grounded(self.plan)
                intent.assert_not_called()
            finally: release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(client.suggest_intent("credits", {"course.one": "x"}))
        self.now += 60
        with patch.object(client, "_request_intent", return_value=None): client.suggest_intent("credits", {"course.one": "x"})
        other = copy.deepcopy(self.plan); other["claims"][0]["rule_sha256"] = "0" * 64
        other["basis_sha256"] = canonical_sha256({k: v for k, v in other.items() if k != "basis_sha256"})
        with self.assertRaises(llm.LLMBusy): client.generate_grounded(other)

    def test_failure_cooldown_no_retry_and_unsupported_provider(self):
        client = self.client(raw=URLError("offline"))
        self.assert_fallback(generate_grounded(self.base, self.registry, client), "unavailable")
        self.now += 59
        self.assert_fallback(generate_grounded(self.base, self.registry, client), "busy")
        self.assertEqual(1, len(client._opener.calls)); self.assertFalse(llm._budget.active)
        self.now += 1
        self.assert_fallback(generate_grounded(self.base, self.registry, client), "unavailable")
        self.assertEqual(2, len(client._opener.calls))
        unsupported = llm.LocalLLMClient(llm.LLMSettings("lmstudio", "http://127.0.0.1:1234", "x"))
        unsupported._opener = FakeOpener(b"must not call")
        with self.assertRaises(llm.LLMUnavailable): unsupported.generate_grounded(self.plan)
        self.assertEqual([], unsupported._opener.calls)

    def test_failed_semantic_generation_never_cached(self):
        doc = document(self.plan); doc["sentences"][0]["text"] = "졸업이 가능합니다."
        client = self.client(doc)
        self.assert_fallback(generate_grounded(self.base, self.registry, client), "rejected")
        self.assertEqual(0, len(llm._budget.generation_cache)); self.assertFalse(llm._budget.active)
        self.assert_fallback(generate_grounded(self.base, self.registry, client), "busy")

    def test_unsupported_statuses_never_send_or_change_base(self):
        client = self.client()
        for status in ("insufficient_evidence", "conflict", "out_of_scope"):
            base = self.base.model_copy(deep=True); base.status = status; base.evidence_packet.status = status
            original = base.model_dump()
            self.assert_fallback(generate_grounded(base, self.registry, client), "not_applicable")
            self.assertEqual(original, base.model_dump())
        self.assertEqual([], client._opener.calls)

    def test_plan_claim_and_source_text_bounds_no_calls(self):
        client = self.client()
        base = self.base.model_copy(deep=True)
        base.evidence_packet.applied_rules *= 6
        self.assert_fallback(generate_grounded(base, self.registry, client), "not_applicable")
        registry, base = copy.deepcopy(self.registry), self.base.model_copy(deep=True)
        rule_id = base.evidence_packet.applied_rules[0].rule_id
        registry.rules[rule_id]["decision"]["statement"] = "승인문장" * 451
        registry.rule_hashes[rule_id] = canonical_sha256(registry.rules[rule_id])
        base.evidence_packet.applied_rules[0].rule_sha256 = registry.rule_hashes[rule_id]
        self.assert_fallback(generate_grounded(base, registry, client), "not_applicable")
        self.assertEqual([], client._opener.calls)

    def test_deployment_flag_strict_default_and_invalid_fail_closed(self):
        self.assertFalse(grounded_generation_enabled({})); self.assertTrue(grounded_generation_enabled({"ACADEMIC_LLM_GROUNDED_GENERATION": "1"}))
        for value in ("true", "yes", " 1", "2", ""):
            with self.assertRaises(ValueError): grounded_generation_enabled({"ACADEMIC_LLM_GROUNDED_GENERATION": value})
        client = self.client()
        with patch.dict("os.environ", {"ACADEMIC_LLM_GROUNDED_GENERATION": "invalid"}):
            chat = GroundedChatEngine(self.engine, client).chat(request(generate_answer=True))
        self.assertEqual("disabled", chat.generation_status); self.assertEqual([], client._opener.calls)

    def test_opt_in_generation_preserves_trusted_base_and_legacy_default_wire(self):
        client = self.client(document(self.plan, alternate=True)); facade = GroundedChatEngine(self.engine, client)
        with patch.dict("os.environ", {"ACADEMIC_LLM_GROUNDED_GENERATION": "1"}):
            legacy = facade.chat(AcademicAnswerRequest.model_validate(request().model_dump(exclude={"previous_question", "generate_answer"})))
            default = facade.chat(request())
            self.assertEqual(legacy.model_dump(exclude_none=True), default.model_dump(exclude_none=True))
            self.assertNotIn("generation_status", default.model_dump(exclude_none=True))
            self.assertEqual([], client._opener.calls)
            generated = facade.chat(request(generate_answer=True))
            cached = facade.chat(request(generate_answer=True))
        self.assertEqual("generated", generated.generation_status); self.assertEqual("cached", cached.generation_status)
        for key, value in self.base.model_dump().items(): self.assertEqual(value, generated.model_dump()[key])
        self.assertEqual(generated.generated_answer, cached.generated_answer)

    def test_disabled_request_optin_and_followup_remain_local(self):
        client = self.client(); facade = GroundedChatEngine(self.engine, client)
        with patch.dict("os.environ", {"ACADEMIC_LLM_GROUNDED_GENERATION": "0"}):
            response = facade.chat(request(generate_answer=True))
        self.assertEqual("disabled", response.generation_status); self.assertEqual([], response.generated_claim_ids)
        self.assertIsNone(response.generated_answer)
        with patch.dict("os.environ", {"ACADEMIC_LLM_GROUNDED_GENERATION": "1"}):
            response = facade.chat(request("그럼 몇 학점인가요?", previous_question="졸업 총학점 기준", generate_answer=True))
        self.assertEqual("not_applicable", response.generation_status); self.assertEqual([], client._opener.calls)

    def test_all_fallback_statuses_preserve_base_without_generated_text(self):
        for status, failure in (("unavailable", llm.LLMUnavailable("offline")), ("rejected", llm.LLMInvalidResponse("bad")), ("busy", llm.LLMBusy("busy"))):
            client = self.client()
            with patch.dict("os.environ", {"ACADEMIC_LLM_GROUNDED_GENERATION": "1"}), patch.object(client, "generate_grounded", side_effect=failure):
                response = GroundedChatEngine(self.engine, client).chat(request(generate_answer=True))
            self.assertEqual(status, response.generation_status); self.assertEqual([], response.generated_claim_ids)
            self.assertIsNone(response.generated_answer)
            for key, value in self.base.model_dump().items(): self.assertEqual(value, response.model_dump()[key])

    def test_response_model_requires_status_supported_coverage_and_empty_fallback(self):
        legacy = GroundedChatEngine(self.engine, self.client()).chat(request()).model_dump()
        for fields in ({"generated_answer": "text"},
                       {"generation_status": "generated", "generated_answer": "text", "generated_claim_ids": []},
                       {"generation_status": "busy", "generated_answer": "text", "generated_claim_ids": []},
                       {"generation_status": "cached", "generated_answer": "text", "generated_claim_ids": ["unknown"]},
                       {"generation_status": "disabled"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                GroundedChatResponse.model_validate({**legacy, **fields})


class GroundedTransportDeadlineTests(unittest.TestCase):
    """Owned ephemeral loopback fixtures only; no real inference/service."""

    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load(ROOT)
        cls.base = AnswerEngine(cls.registry).answer(request())
        cls.plan = build_public_plan(cls.base, cls.registry)

    def setUp(self):
        budget = patch.object(llm, "_budget", llm._RequestBudget())
        budget.start(); self.addCleanup(budget.stop)
        self.raw = json.dumps({"response": json.dumps(document(self.plan), ensure_ascii=False)}, ensure_ascii=False).encode()

    def server(self, chunks, *, interval=0):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(2)
        stop, received, disconnected = threading.Event(), threading.Event(), threading.Event()
        capture, errors = [], []
        def serve():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(1)
                    wire = bytearray()
                    while True:
                        part = connection.recv(8192)
                        if not part: return
                        wire.extend(part)
                        separator = wire.find(b"\r\n\r\n")
                        if separator >= 0:
                            header = bytes(wire[:separator]).decode("ascii")
                            length = int(next(line.partition(":")[2] for line in header.split("\r\n")
                                              if line.lower().startswith("content-length:")))
                            if len(wire) >= separator + 4 + length: break
                        if len(wire) > 100000: raise AssertionError("unbounded synthetic request")
                    capture.append(bytes(wire)); received.set()
                    for chunk in chunks:
                        if stop.wait(interval): break
                        connection.sendall(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                disconnected.set()
            except OSError as exc:
                if not stop.is_set(): errors.append(exc)
            except Exception as exc:
                errors.append(exc)
            finally:
                listener.close()
        worker = threading.Thread(target=serve, name="owned-grounded-fixture")
        worker.start()
        def cleanup():
            stop.set(); listener.close(); worker.join(2)
            self.assertFalse(worker.is_alive(), "synthetic server must be owned and joined")
            self.assertEqual([], errors)
        self.addCleanup(cleanup)
        return listener.getsockname()[1], received, disconnected, capture

    def client(self, port, *, timeout=.16, max_bytes=8192):
        return llm.LocalLLMClient(llm.LLMSettings("ollama", f"http://127.0.0.1:{port}", "fixture-only",
                                                   timeout_seconds=timeout, max_response_bytes=max_bytes,
                                                   min_interval_seconds=60))

    def test_valid_http10_length_and_close_delimited_response(self):
        for length_header in (b"", f"Content-Length: {len(self.raw)}\r\n".encode()):
            with self.subTest(length_header=length_header):
                llm._budget = llm._RequestBudget()
                port, received, _, capture = self.server([b"HTTP/1.0 200 OK\r\n" + length_header + b"\r\n" + self.raw])
                client = self.client(port, timeout=1)
                self.assertFalse(client.generate_grounded(self.plan).cached)
                self.assertTrue(received.wait(1))
                self.assertTrue(capture[0].startswith(b"POST /api/generate HTTP/1.0\r\n"))
                self.assertIn(b"Connection: close\r\n", capture[0])
                payload = json.loads(capture[0].partition(b"\r\n\r\n")[2])
                self.assertFalse(payload["stream"])
                self.assertIn(self.plan["basis_sha256"], payload["prompt"])

    def test_trickled_headers_stop_at_absolute_deadline_and_close_socket(self):
        port, received, disconnected, _ = self.server([b"H"] * 1000, interval=.01)
        client = self.client(port)
        started = time.monotonic()
        outcome = generate_grounded(self.base, self.registry, client)
        elapsed = time.monotonic() - started
        self.assertTrue(received.is_set()); self.assertEqual("unavailable", outcome.generation_status)
        self.assertIsNone(outcome.generated_answer); self.assertLess(elapsed, .65)
        self.assertGreaterEqual(elapsed, .12)
        self.assertFalse(llm._budget.active); self.assertEqual(0, len(llm._budget.generation_cache))
        self.assertGreater(llm._budget.next_allowed - time.monotonic(), 59)
        self.assertTrue(disconnected.wait(.5), "deadline must close the owned connection")
        self.assertEqual("busy", generate_grounded(self.base, self.registry, client).generation_status)

    def test_trickled_body_is_deadline_bounded_and_shared_budget_refuses_queue(self):
        header = f"HTTP/1.1 200 OK\r\nContent-Length: {len(self.raw)}\r\n\r\n".encode()
        port, received, disconnected, _ = self.server([header] + [bytes([byte]) for byte in self.raw], interval=.01)
        client = self.client(port)
        outcomes = []
        started = time.monotonic()
        worker = threading.Thread(target=lambda: outcomes.append(generate_grounded(self.base, self.registry, client)))
        worker.start()
        try:
            self.assertTrue(received.wait(1))
            self.assertEqual("busy", generate_grounded(self.base, self.registry, client).generation_status)
        finally:
            worker.join(1)
        self.assertFalse(worker.is_alive()); self.assertLess(time.monotonic() - started, .65)
        self.assertEqual("unavailable", outcomes[0].generation_status)
        self.assertIsNone(outcomes[0].generated_answer); self.assertFalse(llm._budget.active)
        self.assertTrue(disconnected.wait(.5)); self.assertEqual(0, len(llm._budget.generation_cache))

    def test_non200_redirect_and_ambiguous_framing_fail_closed(self):
        frames = ((b"HTTP/1.0 302 Found\r\nLocation: http://127.0.0.1:1/\r\n\r\n", "unavailable"),
                  (b"HTTP/1.0 500 Error\r\n\r\n", "unavailable"),
                  (b"HTTP/1.0 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n", "rejected"),
                  (b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}", "rejected"),
                  (b"HTTP/1.0 200 OK\r\nContent-Length: 2, 2\r\n\r\n{}", "rejected"),
                  (b"HTTP/1.0 200 OK\r\nContent-Length: 9000\r\n\r\n", "rejected"),
                  (b"HTTP/1.0 200 OK\r\nContent-Length: 5\r\n\r\n{}", "unavailable"),
                  (b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}extra", "rejected"))
        for frame, status in frames:
            with self.subTest(frame=frame[:80]):
                llm._budget = llm._RequestBudget()
                port, _, _, _ = self.server([frame])
                outcome = generate_grounded(self.base, self.registry, self.client(port, timeout=1))
                self.assertEqual(status, outcome.generation_status)
                self.assertIsNone(outcome.generated_answer); self.assertFalse(llm._budget.active)
                self.assertEqual(0, len(llm._budget.generation_cache))

    def test_header_and_close_delimited_body_are_bounded(self):
        for frame in (b"HTTP/1.0 200 OK\r\nX-Test: " + b"x" * 8192 + b"\r\n\r\n",
                      b"HTTP/1.0 200 OK\r\n\r\n" + b"x" * 129):
            with self.subTest(frame=frame[:30]):
                llm._budget = llm._RequestBudget()
                port, _, _, _ = self.server([frame])
                outcome = generate_grounded(self.base, self.registry, self.client(port, timeout=1, max_bytes=128))
                self.assertEqual("rejected", outcome.generation_status)
                self.assertIsNone(outcome.generated_answer); self.assertFalse(llm._budget.active)

    def test_https_generation_is_unsupported_without_socket_or_budget(self):
        client = llm.LocalLLMClient(llm.LLMSettings("ollama", "https://127.0.0.1:11434", "fixture-only"))
        with patch.object(llm.socket, "socket") as connect:
            outcome = generate_grounded(self.base, self.registry, client)
        connect.assert_not_called(); self.assertEqual("unavailable", outcome.generation_status)
        self.assertFalse(llm._budget.active); self.assertEqual(0, llm._budget.next_allowed)


if __name__ == "__main__":
    unittest.main()
