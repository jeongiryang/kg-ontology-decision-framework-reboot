"""Independent final-generation boundary QA; all inference is synthetic."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
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
from academic_assistant.core import AnswerEngine, canonical_response_json
from academic_assistant.grounded_chat import GroundedChatEngine
from academic_assistant.grounded_generation import build_public_plan, generate_grounded, grounded_generation_enabled
from academic_assistant.llm import LLMBusy, LLMInvalidResponse, LLMSettings, LLMUnavailable, LocalLLMClient, VerifiedGeneration
from academic_assistant.models import AcademicChatRequest
from academic_assistant.registry import Registry, canonical_sha256


def request(question="졸업학점은 얼마인가요?", **updates):
    value = {"schema_version": "1.0.0", "question": question, "admission_year": 2026,
             "matched_curriculum_year": 2026, "department": "컴퓨터공학과", "earned_credits": {}}
    value.update(updates)
    return AcademicChatRequest.model_validate(value)


def document(plan):
    # Independently encode a public claim, rather than calling the validator's
    # permitted-sentence helper to manufacture the expected output.
    sentences = []
    for claim in plan["claims"]:
        text = claim["statement"]
        if claim.get("metric") == "credits.graduation.total":
            text = f"졸업 총학점 기준은 {claim['credits']}학점 이상입니다."
        sentences.append({"claim_id": claim["claim_id"], "text": text})
    return {"basis_sha256": plan["basis_sha256"], "introduction": "확인된 기준을 안내해 드리겠습니다.",
            "sentences": sentences}


def rendered(value):
    return "\n".join([value["introduction"], *[item["text"] for item in value["sentences"]]])


class Wire:
    def __init__(self, transform=lambda value: value, raw=None):
        self.transform, self.raw, self.calls = transform, raw, []

    def __call__(self, transport_request):
        payload = json.loads(transport_request.data)
        plan = json.loads(payload["prompt"])["plan"]
        self.calls.append((payload, transport_request.full_url))
        raw = self.raw
        if raw is None:
            value = self.transform(document(plan))
            raw = json.dumps({"response": json.dumps(value, ensure_ascii=False)}, ensure_ascii=False).encode()
        return raw


class DocumentClient:
    def __init__(self, operation=None, error=None):
        self.operation, self.error, self.calls = operation, error, []

    def generate_grounded(self, plan):
        self.calls.append(deepcopy(plan))
        if self.error is not None:
            raise self.error
        value = document(plan)
        if self.operation is not None:
            return self.operation(plan, value)
        return VerifiedGeneration(rendered(value), tuple(item["claim_id"] for item in value["sentences"]), False, value)

    def suggest_intent(self, *_):
        return None


class GemmaFinalIndependentQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load(ROOT)
        cls.engine = AnswerEngine(cls.registry)
        cls.request_schema = json.loads((ROOT / "contracts/academic-chat-request.schema.json").read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "contracts/academic-chat-response.schema.json").read_text(encoding="utf-8"))
        evidence = json.loads((ROOT / "contracts/evidence-packet.schema.json").read_text(encoding="utf-8"))
        resources = SchemaRegistry().with_resource(evidence["$id"], Resource.from_contents(evidence))
        cls.response_validator = Draft202012Validator(schema, registry=resources)

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ACADEMIC_LLM_GROUNDED_GENERATION": "1", "ACADEMIC_LLM_PROVIDER": "disabled"}).start()
        patch("academic_assistant.llm._budget", llm._RequestBudget()).start()

    def client(self, transform=lambda value: value, raw=None):
        client = LocalLLMClient(LLMSettings("ollama", "http://127.0.0.1:11434", "synthetic-gemma", max_response_bytes=32768))
        wire = Wire(transform, raw)
        client._post_grounded = wire
        return client, wire

    def chat(self, payload, client, engine=None):
        return GroundedChatEngine(engine or self.engine, client).chat(payload)

    def assert_base(self, base, response):
        for key, value in base.model_dump(mode="json").items():
            self.assertEqual(value, response.model_dump(mode="json")[key], key)

    def test_actual_transport_text_survives_and_base_is_not_replaced(self):
        client, wire = self.client()
        payload = request(generate_answer=True)
        base = self.engine.answer(payload)
        result = self.chat(payload, client)
        self.assertEqual("generated", result.generation_status)
        self.assertEqual("확인된 기준을 안내해 드리겠습니다.\n졸업 총학점 기준은 130학점 이상입니다.", result.generated_answer)
        self.assertNotEqual(base.answer, result.generated_answer)
        self.assertEqual([item.rule_id for item in base.evidence_packet.applied_rules], result.generated_claim_ids)
        self.assert_base(base, result)
        self.assertEqual(1, len(wire.calls))
        self.response_validator.validate(result.model_dump(mode="json", exclude_none=True))

    def test_transport_semantic_mutations_are_rejected_not_displayed(self):
        mutations = {
            "changed number": "졸업 총학점 기준은 129학점 이상입니다.",
            "lost minimum comparator": "졸업 총학점 기준은 130학점입니다.",
            "negated requirement": "졸업 총학점은 최소 130학점을 이수하지 않아도 됩니다.",
            "extra graduation determination": "졸업 총학점 기준은 130학점 이상입니다. 그러므로 졸업할 수 있습니다.",
            "wrong grammatical particle": "졸업 총학점는 최소 130학점을 이수해야 합니다.",
            "wrong subject": "교양 총학점 기준은 130학점 이상입니다.",
        }
        payload = request(generate_answer=True)
        base = self.engine.answer(payload)
        for name, text in mutations.items():
            with self.subTest(name=name), patch("academic_assistant.llm._budget", llm._RequestBudget()):
                def change(value):
                    value["sentences"][0]["text"] = text
                    return value
                client, wire = self.client(change)
                result = self.chat(payload, client)
                self.assertEqual("rejected", result.generation_status)
                self.assertIsNone(result.generated_answer)
                self.assertEqual([], result.generated_claim_ids)
                self.assert_base(base, result)
                self.assertEqual(1, len(wire.calls))

    def test_ordered_bundle_rejects_drop_extra_duplicate_and_swap(self):
        payload = request("교양학점 기준", generate_answer=True)
        base = self.engine.answer(payload)
        self.assertGreater(len(base.evidence_packet.applied_rules), 1)
        for mutation in ("drop", "extra", "duplicate", "swap", "basis", "field"):
            with self.subTest(mutation=mutation), patch("academic_assistant.llm._budget", llm._RequestBudget()):
                def change(value):
                    if mutation == "drop": value["sentences"].pop()
                    elif mutation == "extra": value["sentences"].append(deepcopy(value["sentences"][0]))
                    elif mutation == "duplicate": value["sentences"][1] = deepcopy(value["sentences"][0])
                    elif mutation == "swap": value["sentences"].reverse()
                    elif mutation == "basis": value["basis_sha256"] = "0" * 64
                    else: value["answer"] = "졸업 확정"
                    return value
                client, _ = self.client(change)
                result = self.chat(payload, client)
                self.assertEqual("rejected", result.generation_status)
                self.assertIsNone(result.generated_answer)
                self.assert_base(base, result)

    def test_none_listed_and_operational_caveats_cannot_be_erased(self):
        for question, erased in (
            ("졸업논문대체 기준", "컴퓨터공학과 졸업논문은 대체할 수 없다."),
            ("코딩 테스트 미통과 시 처리", "코딩 테스트 미통과자는 캡스톤디자인 I에서 U 처리되고 다음 연도 캡스톤디자인 II를 수강할 수 없다."),
        ):
            with self.subTest(question=question), patch("academic_assistant.llm._budget", llm._RequestBudget()):
                payload = request(question, generate_answer=True)
                base = self.engine.answer(payload)
                self.assertEqual("supported", base.status)
                def change(value):
                    value["sentences"][0]["text"] = erased
                    return value
                client, _ = self.client(change)
                result = self.chat(payload, client)
                self.assertEqual("rejected", result.generation_status)
                self.assert_base(base, result)

    def test_required_course_labels_are_all_in_accepted_model_text(self):
        client, _ = self.client()
        result = self.chat(request("전공필수과목목록 기준", generate_answer=True), client)
        self.assertEqual("generated", result.generation_status)
        rule = self.registry.rules["cwnu.cs.2026.major-required-course-set"]
        labels = [item["label"] for item in rule["decision"]["outcome"]["items"]]
        self.assertEqual(9, len(labels))
        for label in labels:
            self.assertIn(label, result.generated_answer)

    def test_earned_values_questions_and_transcript_sentinels_never_enter_prompt_or_cache(self):
        client, wire = self.client()
        results = []
        for earned in (107, 119):
            payload = request("졸업학점 얼마나 부족해", previous_question="전공선택 학점 기준 확인", generate_answer=True,
                              earned_credits={"credits.graduation.total": earned})
            results.append(self.chat(payload, client))
        self.assertEqual(["generated", "cached"], [item.generation_status for item in results])
        self.assertEqual([23, 11], [item.calculations[0].gap for item in results])
        self.assertEqual(1, len(wire.calls))
        payload, endpoint = wire.calls[0]
        encoded = json.dumps(payload, ensure_ascii=False)
        cache = repr(llm._budget.generation_cache)
        for forbidden in ("졸업학점 얼마나 부족해", "전공선택 학점 기준 확인", "earned_credits", "student_facts",
                          "previous_question", "transcript", results[0].packet_id, "generated_answer"):
            self.assertNotIn(forbidden, encoded)
            self.assertNotIn(forbidden, cache)
        task = json.loads(payload["prompt"])
        plan = task["plan"]
        self.assertEqual({"grammar_version", "basis_sha256", "scope", "claims"}, set(plan))
        self.assertEqual(130, plan["claims"][0]["credits"])
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        self.assertEqual(0, payload["options"]["temperature"])
        self.assertLessEqual(payload["options"]["num_predict"], 512)
        self.assertEqual("60s", payload["keep_alive"])
        self.assertLessEqual(client.settings.timeout_seconds, 30)
        self.assertEqual("http://127.0.0.1:11434/api/generate", endpoint)

    def test_default_and_explicit_false_have_exact_legacy_wire_shape(self):
        client = DocumentClient()
        with patch.dict(os.environ, {"ACADEMIC_LLM_GROUNDED_GENERATION": "1"}):
            omitted = self.chat(request(), client)
            explicit = self.chat(request(generate_answer=False), client)
        expected = {**self.engine.answer(request()).model_dump(mode="json"), "llm_status": "skipped",
                    "clarification_choices": [], "context_used": False}
        self.assertEqual(expected, omitted.model_dump(mode="json", exclude_none=True))
        self.assertEqual(canonical_response_json(omitted), canonical_response_json(explicit))
        self.assertEqual([], client.calls)

    def test_every_generation_fallback_preserves_base_and_has_no_text(self):
        for expected, error in (("busy", LLMBusy("synthetic")), ("unavailable", LLMUnavailable("synthetic")),
                                ("rejected", LLMInvalidResponse("synthetic")), ("unavailable", RuntimeError("synthetic"))):
            with self.subTest(expected=expected, error=type(error).__name__):
                payload = request(generate_answer=True)
                base = self.engine.answer(payload)
                result = self.chat(payload, DocumentClient(error=error))
                self.assertEqual(expected, result.generation_status)
                self.assertEqual([], result.generated_claim_ids)
                self.assertNotIn("generated_answer", result.model_dump(exclude_none=True))
                self.assert_base(base, result)

    def test_disabled_invalid_config_and_no_client_are_fail_closed(self):
        for setting in ("0", "true", "2", ""):
            with self.subTest(setting=setting), patch.dict(os.environ, {"ACADEMIC_LLM_GROUNDED_GENERATION": setting}):
                client = DocumentClient()
                result = self.chat(request(generate_answer=True), client)
                self.assertEqual("disabled", result.generation_status)
                self.assertEqual([], client.calls)
                self.assert_base(self.engine.answer(request()), result)
        self.assertFalse(grounded_generation_enabled({}))
        self.assertTrue(grounded_generation_enabled({"ACADEMIC_LLM_GROUNDED_GENERATION": "1"}))
        with self.assertRaises(ValueError): grounded_generation_enabled({"ACADEMIC_LLM_GROUNDED_GENERATION": "yes"})
        result = GroundedChatEngine(self.engine).chat(request(generate_answer=True))
        self.assertEqual("unavailable", result.generation_status)
        self.assertIsNone(result.generated_answer)
        self.assert_base(self.engine.answer(request()), result)

    def test_non_supported_packets_and_context_followups_never_generate(self):
        conflict = replace(self.registry, conflicts={"credit_threshold:credits.general.foundation": ("cwnu.cs.2026.credits.general-foundation",)})
        cases = ((self.engine, request("PCCP 400점이 캡스톤 합격인가요?", generate_answer=True), "insufficient_evidence"),
                 (self.engine, request(admission_year=2025, generate_answer=True), "out_of_scope"),
                 (AnswerEngine(conflict), request("기초교양 기준", generate_answer=True), "conflict"))
        for engine, payload, status in cases:
            with self.subTest(status=status):
                client = DocumentClient()
                result = self.chat(payload, client, engine)
                self.assertEqual(status, result.status)
                self.assertEqual("not_applicable", result.generation_status)
                self.assertEqual([], client.calls)
                self.assert_base(engine.answer(payload), result)
        client = DocumentClient()
        result = self.chat(request("몇학점 남았어", previous_question="졸업학점", generate_answer=True,
                                   earned_credits={"credits.graduation.total": 107}), client)
        self.assertEqual("not_applicable", result.generation_status)
        self.assertTrue(result.context_used)
        self.assertEqual([], client.calls)
        deterministic = self.chat(request("몇학점 남았어", previous_question="졸업학점",
                                          earned_credits={"credits.graduation.total": 107}), client)
        for key in self.engine.answer(request()).model_dump():
            self.assertEqual(deterministic.model_dump()[key], result.model_dump()[key], key)

    def test_consumer_rejects_forged_verified_text_ids_and_cached_type(self):
        for mutation in ("text", "ids", "cache"):
            with self.subTest(mutation=mutation):
                def forge(plan, value):
                    text = rendered(value) + ("\n졸업 확정" if mutation == "text" else "")
                    ids = ("cwnu.cs.2026.invented",) if mutation == "ids" else tuple(item["claim_id"] for item in value["sentences"])
                    return VerifiedGeneration(text, ids, "cached" if mutation == "cache" else False, value)
                payload = request(generate_answer=True)
                result = self.chat(payload, DocumentClient(forge))
                self.assertEqual("rejected", result.generation_status)
                self.assert_base(self.engine.answer(payload), result)
                self.assertIsNone(result.generated_answer)

    def test_approval_hash_source_scope_and_midflight_mutations_are_rejected(self):
        for mutation in ("rule approval", "rule hash", "source approval", "rule scope", "packet scope", "midflight"):
            with self.subTest(mutation=mutation):
                registry = replace(self.registry, rules=deepcopy(self.registry.rules), sources=deepcopy(self.registry.sources),
                                   rule_hashes=dict(self.registry.rule_hashes))
                base = self.engine.answer(request()).model_copy(deep=True)
                before = canonical_response_json(base)
                rid = base.evidence_packet.applied_rules[0].rule_id
                rule = registry.rules[rid]
                if mutation == "rule approval": rule["review"]["status"] = "pending"
                elif mutation == "rule hash": base.evidence_packet.applied_rules[0].rule_sha256 = "0" * 64
                elif mutation == "source approval": registry.sources[rule["evidence"][0]["source_id"]]["review"]["status"] = "pending"
                elif mutation == "rule scope": rule["applicability"]["admission_years"] = [2025]
                elif mutation == "packet scope": base.evidence_packet.scope.admission_year = 2025
                if mutation in {"rule approval", "rule scope"}:
                    # Matching hashes must not bypass independent approval/scope
                    # enforcement after a malicious registry replacement.
                    registry.rule_hashes[rid] = canonical_sha256(rule)
                    base.evidence_packet.applied_rules[0].rule_sha256 = registry.rule_hashes[rid]
                def during(plan, value):
                    rule["review"]["status"] = "pending"
                    return VerifiedGeneration(rendered(value), tuple(item["claim_id"] for item in value["sentences"]), False, value)
                client = DocumentClient(during if mutation == "midflight" else None)
                mutated_before = canonical_response_json(base)
                result = generate_grounded(base, registry, client)
                self.assertEqual("rejected", result.generation_status)
                self.assertIsNone(result.generated_answer)
                self.assertEqual(mutated_before, canonical_response_json(base))
                if mutation not in {"rule hash", "packet scope", "rule approval", "rule scope"}:
                    self.assertEqual(before, canonical_response_json(base))

    def test_wire_invalid_json_duplicate_properties_utf8_and_byte_limit_reject(self):
        invalid = (b'{', b'\xff', b'{"response":"{}","response":"{}"}', b'x' * 32769,
                   b'{"response":"{\\"basis_sha256\\":\\"x\\",\\"basis_sha256\\":\\"y\\"}"}')
        for raw in invalid:
            with self.subTest(raw=raw[:32]), patch("academic_assistant.llm._budget", llm._RequestBudget()):
                client, _ = self.client(raw=raw)
                result = self.chat(request(generate_answer=True), client)
                self.assertEqual("rejected", result.generation_status)
                self.assertIsNone(result.generated_answer)

    def test_real_owned_trickle_provider_hits_total_deadline_and_releases_budget(self):
        stop = threading.Event()
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append(payload)
                plan = json.loads(payload["prompt"])["plan"]
                body = json.dumps({"response": json.dumps(document(plan), ensure_ascii=False)}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    for value in body:
                        if stop.wait(.025):
                            return
                        self.wfile.write(bytes([value]))
                        self.wfile.flush()
                except OSError:
                    return  # Our client closed its connection at the deadline.

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 1
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            settings = LLMSettings("ollama", f"http://127.0.0.1:{server.server_port}", "synthetic-gemma",
                                   timeout_seconds=.25, max_response_bytes=32768)
            client = LocalLLMClient(settings)
            payload = request(generate_answer=True)
            started = time.monotonic()
            result = self.chat(payload, client)
            elapsed = time.monotonic() - started
            self.assertEqual("unavailable", result.generation_status)
            self.assertIsNone(result.generated_answer)
            self.assertEqual([], result.generated_claim_ids)
            self.assert_base(self.engine.answer(payload), result)
            self.assertGreaterEqual(elapsed, .20)
            self.assertLess(elapsed, .75, "trickled bytes extended the total configured inference deadline")
            self.assertFalse(llm._budget.active, "deadline left shared inference capacity active")
            self.assertGreater(llm._budget.next_allowed, time.monotonic())
            with self.assertRaises(LLMBusy):
                client.generate_grounded(build_public_plan(self.engine.answer(payload), self.registry))
            self.assertEqual(1, len(captured), "timeout retried or queued another provider request")
        finally:
            stop.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), "owned synthetic provider did not shut down")

    def test_request_boolean_and_response_fallback_schema_are_strict(self):
        valid = request().model_dump(mode="json", exclude_none=True)
        Draft202012Validator(self.request_schema).validate(valid)
        for wrong in ("true", 1, None, [], {}):
            with self.subTest(wrong=wrong):
                with self.assertRaises(ValidationError): AcademicChatRequest.model_validate({**valid, "generate_answer": wrong})
                with self.assertRaises(SchemaError): Draft202012Validator(self.request_schema).validate({**valid, "generate_answer": wrong})
        result = self.chat(request(generate_answer=True), DocumentClient()).model_dump(mode="json", exclude_none=True)
        self.response_validator.validate(result)
        for status in ("disabled", "unavailable", "rejected", "busy", "not_applicable"):
            fallback = {**result, "generation_status": status, "generated_claim_ids": []}
            fallback.pop("generated_answer")
            self.response_validator.validate(fallback)
            with self.assertRaises(SchemaError): self.response_validator.validate({**fallback, "generated_answer": "not verified"})
        for mutation in ({"status": "conflict"}, {"generated_claim_ids": []}, {"generated_answer": None}):
            with self.subTest(mutation=mutation), self.assertRaises(SchemaError): self.response_validator.validate({**result, **mutation})

    def test_actual_api_opt_in_default_answers_and_invalid_bool(self):
        model = DocumentClient()
        facade = GroundedChatEngine(self.engine, model)
        valid = request().model_dump(mode="json", exclude_none=True)
        with TestClient(app) as client, patch("academic_assistant.api._chat_engine", return_value=facade), \
                patch("academic_assistant.api._engine", return_value=self.engine):
            legacy = client.post("/v1/academic/chat", json=valid)
            explicit = client.post("/v1/academic/chat", json={**valid, "generate_answer": False})
            self.assertEqual(legacy.content, explicit.content)
            self.assertNotIn("generation_status", legacy.json())
            answer_payload = {key: value for key, value in valid.items() if key not in {"previous_question", "generate_answer"}}
            deterministic = client.post("/v1/academic/answers", json=answer_payload)
            self.assertEqual(200, deterministic.status_code)
            for key, value in deterministic.json().items(): self.assertEqual(value, legacy.json()[key])
            rejected_answer_flag = client.post("/v1/academic/answers", json={**answer_payload, "generate_answer": True})
            self.assertEqual(422, rejected_answer_flag.status_code)
            generated = client.post("/v1/academic/chat", json={**valid, "generate_answer": True})
            self.assertEqual(200, generated.status_code)
            self.assertEqual("generated", generated.json()["generation_status"])
            self.response_validator.validate(generated.json())
            invalid = client.post("/v1/academic/chat", json={**valid, "generate_answer": "true"})
            self.assertEqual(422, invalid.status_code)
            self.assertEqual({"detail": "invalid request"}, invalid.json())
        self.assertEqual(1, len(model.calls))


if __name__ == "__main__":
    unittest.main()
