from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry as SchemaRegistry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.api import app
from academic_assistant.core import AnswerEngine, SANITIZED_DEPARTMENT, canonical_response_json
from academic_assistant.models import AcademicAnswerRequest
from academic_assistant.registry import Registry, RegistryUnavailable, canonical_sha256


def request(question: str, **updates) -> AcademicAnswerRequest:
    payload = {"question": question, "admission_year": 2026, "matched_curriculum_year": 2026, "department": "컴퓨터공학과", "earned_credits": {}}
    payload.update(updates)
    return AcademicAnswerRequest.model_validate(payload)


class AcademicAnswerEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = Registry.load(ROOT)
        cls.engine = AnswerEngine(cls.registry)
        cls.evidence_schema = json.loads((ROOT / "contracts/evidence-packet.schema.json").read_text(encoding="utf-8"))
        cls.response_schema = json.loads((ROOT / "contracts/academic-answer-response.schema.json").read_text(encoding="utf-8"))

    def test_registry_exact_profile_and_canonical_hashes(self) -> None:
        self.assertEqual(13, len(self.registry.rules))
        self.assertEqual({canonical_sha256(rule) for rule in self.registry.rules.values()}, set(self.registry.rule_hashes.values()))
        self.assertEqual(set(), set(self.registry.conflicts))

    def _mutated_registry(self, relative_path: str, mutate) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("contracts", "config", "knowledge", "reviews"):
                shutil.copytree(ROOT / name, root / name)
            path = root / relative_path
            value = json.loads(path.read_text(encoding="utf-8"))
            mutate(value)
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(RegistryUnavailable, "^academic registry unavailable$"):
                Registry.load(root)

    def test_registry_pins_reject_schema_valid_content_mutations(self) -> None:
        rule = "knowledge/rules/cwnu.cs.2026.credits.graduation-total.json"
        attacks = [
            (rule, lambda value: value["decision"]["outcome"].__setitem__("credits", 131)),
            (rule, lambda value: value["decision"].__setitem__("statement", "졸업을 위해 총 131학점 이상 이수해야 한다.")),
            (rule, lambda value: value["review"].__setitem__("rationale", value["review"]["rationale"] + " 변경")),
            (rule, lambda value: value["applicability"]["conditions"].append({"fact_key":"student.transfer","operator":"equals","value":False})),
            (rule, lambda value: value["evidence"][0].__setitem__("locator", "PDF p.1")),
            ("knowledge/sources/cwnu.curriculum.2026.changwon-undergraduate.json", lambda value: value.__setitem__("title", "변경된 교육과정")),
            ("reviews/academic/research/cwnu.cs.2026.graduation-practices.json", lambda value: value["claims"][0].__setitem__("statement", "변경된 미검증 주장")),
            ("config/academic-intents.json", lambda value: value["intents"][0]["aliases"].append("변조별칭")),
        ]
        for path, mutate in attacks:
            with self.subTest(path=path, mutation=str(mutate)):
                self._mutated_registry(path, mutate)

    def test_intent_profile_schema_rejects_missing_or_mistyped_keys(self) -> None:
        self._mutated_registry("config/academic-intents.json", lambda value: value.pop("negation_aliases"))
        self._mutated_registry("config/academic-intents.json", lambda value: value.__setitem__("exception_aliases", "재입학"))

    def test_malformed_intent_profile_fails_readiness(self) -> None:
        from academic_assistant.api import _engine
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("contracts", "config", "knowledge", "reviews"):
                shutil.copytree(ROOT / name, root / name)
            path = root / "config/academic-intents.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value.pop("exception_aliases")
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            _engine.cache_clear()
            original_load = Registry.load
            with patch("academic_assistant.api.Registry.load", side_effect=lambda *args, **kwargs: original_load(root)):
                response = TestClient(app).get("/readyz")
            _engine.cache_clear()
        self.assertEqual(503, response.status_code)
        self.assertEqual('{"detail":"service unavailable"}', response.text)

    def test_supported_response_is_schema_valid_and_deterministic(self) -> None:
        first = self.engine.answer(request("졸업학점은 얼마인가요?"))
        second = self.engine.answer(request("  졸업학점은   얼마인가요? "))
        self.assertEqual("supported", first.status)
        self.assertEqual(first.packet_id, second.packet_id)
        Draft202012Validator(self.evidence_schema).validate(first.evidence_packet.model_dump(mode="json", exclude_none=True))
        schema_registry = SchemaRegistry().with_resource(self.evidence_schema["$id"], Resource.from_contents(self.evidence_schema))
        Draft202012Validator(self.response_schema, registry=schema_registry).validate(first.model_dump(mode="json", exclude_none=True))
        self.assertNotIn(str(ROOT), canonical_response_json(first))
        self.assertNotIn("얼마인가요", canonical_response_json(first))

    def test_specific_intent_suppresses_bundle(self) -> None:
        result = self.engine.answer(request("전공필수 기준"))
        self.assertEqual(["credits.major.required"], result.intent_ids)
        self.assertEqual(1, len(result.evidence_packet.applied_rules))

    def test_general_and_major_bundles_have_fixed_sizes(self) -> None:
        self.assertEqual(4, len(self.engine.answer(request("교양 학점 기준")).evidence_packet.applied_rules))
        self.assertEqual(5, len(self.engine.answer(request("전공 학점 기준")).evidence_packet.applied_rules))

    def test_explicit_conjunction_selects_multiple_specific_intents(self) -> None:
        result = self.engine.answer(request("전공필수 및 전공선택 기준"))
        self.assertEqual({"credits.major.required", "credits.major.elective"}, set(result.intent_ids))

    def test_gap_uses_only_selected_metric_and_clamps_zero(self) -> None:
        result = self.engine.answer(request("졸업학점 얼마나 부족해", earned_credits={"credits.graduation.total": 140}))
        self.assertEqual(0, result.calculations[0].gap)
        self.assertEqual({"credits.graduation.total": 140}, result.evidence_packet.student_facts)

    def test_gap_without_selected_metric_fails_closed(self) -> None:
        result = self.engine.answer(request("졸업학점 얼마나 부족해"))
        self.assertEqual("insufficient_evidence", result.status)
        self.assertEqual([], result.evidence_packet.applied_rules)
        self.assertEqual([], result.evidence_packet.evidence)

    def test_protected_research_precedes_approved_terms(self) -> None:
        result = self.engine.answer(request("공모전 수상으로 졸업논문을 대체할 수 있나요"))
        self.assertEqual("insufficient_evidence", result.status)
        self.assertEqual([], result.evidence_packet.applied_rules)

    def test_protected_research_precedes_unrelated_known_credit_check(self) -> None:
        result = self.engine.answer(request("PCCP 통과 조건", earned_credits={"credits.graduation.total": 120}))
        self.assertEqual("insufficient_evidence", result.status)

    def test_source_scope_exceptions_fail_closed(self) -> None:
        for phrase in ("재입학", "재입학생", "전과", "전과생", "편입", "편입생", "경과조치", "경과 조치 대상"):
            with self.subTest(phrase=phrase):
                result = self.engine.answer(request(f"{phrase} 졸업학점 기준"))
                self.assertEqual("insufficient_evidence", result.status)
                self.assertEqual([], result.evidence_packet.applied_rules)

    def test_negation_and_lexical_embedding_are_ambiguous(self) -> None:
        for question in ("비전공필수 기준", "전공필수 말고 전공선택"):
            with self.subTest(question=question):
                result = self.engine.answer(request(question))
                self.assertEqual("insufficient_evidence", result.status)
                self.assertEqual([], result.evidence_packet.applied_rules)
        self.assertEqual("supported", self.engine.answer(request("전공필수 및 전공선택")).status)

    def test_aliases_do_not_match_inside_unrelated_compounds(self) -> None:
        for question in ("전선풍기 가격", "전공필수품 가격"):
            with self.subTest(question=question):
                result = self.engine.answer(request(question))
                self.assertNotEqual("supported", result.status)
                self.assertEqual([], result.evidence_packet.applied_rules)
                self.assertEqual([], result.evidence_packet.evidence)

    def test_disjunction_is_unresolved_but_conjunction_is_supported(self) -> None:
        result = self.engine.answer(request("전공필수 또는 전공선택 중 하나"))
        self.assertEqual("insufficient_evidence", result.status)
        self.assertEqual([], result.evidence_packet.applied_rules)
        self.assertEqual([], result.evidence_packet.evidence)
        conjunction = self.engine.answer(request("전공필수 및 전공선택 기준"))
        self.assertEqual("supported", conjunction.status)
        self.assertEqual({"credits.major.required", "credits.major.elective"}, set(conjunction.intent_ids))

    def test_short_elective_alias_requires_academic_context(self) -> None:
        for question in ("전선 학점 기준", "전선 전공 기준", "전선 이수 기준"):
            with self.subTest(question=question):
                supported = self.engine.answer(request(question))
                self.assertEqual("supported", supported.status)
                self.assertEqual(["credits.major.elective"], supported.intent_ids)
        for question in ("전선은 구리로 만드나요", "전선 가격", "전선으로 연결하는 기준"):
            with self.subTest(question=question):
                result = self.engine.answer(request(question))
                self.assertNotEqual("supported", result.status)
                self.assertEqual([], result.evidence_packet.applied_rules)
                self.assertEqual([], result.evidence_packet.evidence)

    def test_multiple_specific_intents_require_conjunction_between_every_match(self) -> None:
        ambiguous = (
            "전공필수 전공선택",
            "전공필수, 전공선택",
            "전공필수 / 전공선택",
            "전공필수 vs 전공선택",
            "전공필수 비교 전공선택",
            "전공필수 그리고싶다 전공선택",
            "전공필수 및기준 전공선택",
            "전공필수 그리고 싶다 전공선택",
            "전공필수 아무말 그리고 아무말 전공선택",
            "전공필수 및 기준 전공선택",
            "전공필수 및 전공선택 및",
            "전공필수 및 전공선택 기준 그리고",
            "전공필수과 전공선택",
            "전공필수 전공선택 및 심화전공",
        )
        for question in ambiguous:
            with self.subTest(question=question):
                result = self.engine.answer(request(question))
                self.assertEqual("insufficient_evidence", result.status)
                self.assertEqual([], result.evidence_packet.applied_rules)
                self.assertEqual([], result.evidence_packet.evidence)
        for question in ("전공필수 및 전공선택 기준", "전공필수 그리고 전공선택", "전공필수와 전공선택", "전공선택과 전공필수"):
            with self.subTest(question=question):
                result = self.engine.answer(request(question))
                self.assertEqual("supported", result.status)
                self.assertEqual({"credits.major.required", "credits.major.elective"}, set(result.intent_ids))
        three_way = (
            "전공필수 및 전공선택 및 심화전공",
            "전공필수 그리고 전공선택 그리고 심화전공",
            "전공필수와 전공선택과 심화전공",
        )
        for question in three_way:
            with self.subTest(question=question):
                result = self.engine.answer(request(question))
                self.assertEqual("supported", result.status)
                self.assertEqual({"credits.major.required", "credits.major.elective", "credits.major.advanced"}, set(result.intent_ids))

    def test_thesis_substitution_wording_is_narrow(self) -> None:
        result = self.engine.answer(request("졸업논문 대체요건"))
        self.assertEqual(["graduation.thesis.substitution"], result.intent_ids)
        self.assertIn("현재 교육과정 규정집에는", result.answer)
        self.assertNotIn("면제", result.answer)

    def test_bare_number_does_not_select_intent(self) -> None:
        result = self.engine.answer(request("130"))
        self.assertEqual("out_of_scope", result.status)
        self.assertEqual([], result.intent_ids)

    def test_scope_and_unknown_academic_statuses(self) -> None:
        self.assertEqual("out_of_scope", self.engine.answer(request("졸업학점", admission_year=2025)).status)
        self.assertEqual("insufficient_evidence", self.engine.answer(request("복수전공 신청 기준")).status)

    def test_semantic_conflict_affects_only_related_intent(self) -> None:
        conflicted = replace(self.registry, conflicts={"credit_threshold:credits.graduation.total": ("cwnu.cs.2026.credits.graduation-total", "synthetic")})
        engine = AnswerEngine(conflicted)
        self.assertEqual("conflict", engine.answer(request("졸업학점")).status)
        self.assertEqual("supported", engine.answer(request("전공필수")).status)

    def test_unknown_metric_and_pii_are_invalid(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.answer(request("졸업학점", earned_credits={"unknown.metric": 1}))
        with self.assertRaises(ValueError):
            self.engine.answer(request("학번 2026123456 졸업학점"))
        with self.assertRaises(ValueError):
            self.engine.answer(request("졸업학점", earned_credits={"credits.major.total": 70}))

    def test_api_semantic_status_and_validation(self) -> None:
        client = TestClient(app)
        payload = request("졸업학점").model_dump(mode="json")
        response = client.post("/v1/academic/answers", json=payload)
        self.assertEqual(200, response.status_code)
        self.assertEqual("supported", response.json()["status"])
        self.assertEqual(canonical_response_json(self.engine.answer(request("졸업학점"))), response.text)
        payload["student_name"] = "홍길동"
        invalid = client.post("/v1/academic/answers", json=payload)
        self.assertEqual(422, invalid.status_code)
        self.assertEqual('{"detail":"invalid request"}', invalid.text)
        self.assertNotIn("홍길동", invalid.text)

    def test_api_and_cli_validation_errors_do_not_echo_input(self) -> None:
        client = TestClient(app)
        secret = "학번 2026123456 졸업학점"
        payload = request("졸업학점").model_dump(mode="json")
        payload["question"] = secret
        response = client.post("/v1/academic/answers", json=payload)
        self.assertEqual(422, response.status_code)
        self.assertEqual('{"detail":"invalid request"}', response.text)
        self.assertNotIn(secret, response.text)
        command = [sys.executable, "-m", "academic_assistant", "ask", "--question", secret, "--admission-year", "2026", "--curriculum-year", "2026", "--department", "컴퓨터공학과"]
        completed = subprocess.run(command, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(64, completed.returncode)
        self.assertEqual("invalid request", completed.stderr.strip())
        self.assertNotIn(secret, completed.stderr)
        malformed = [sys.executable, "-m", "academic_assistant", "ask", "--question", "졸업학점", "--admission-year", secret, "--curriculum-year", "2026", "--department", "컴퓨터공학과"]
        completed = subprocess.run(malformed, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(64, completed.returncode)
        self.assertEqual("invalid request", completed.stderr.strip())
        self.assertNotIn(secret, completed.stderr)

    def test_unsupported_department_is_sanitized(self) -> None:
        client = TestClient(app)
        for department in (r"C:\\Users\\student\\record.txt", "/home/student/record.txt", "학번2026123456"):
            with self.subTest(department=department):
                payload = request("졸업학점", department=department).model_dump(mode="json")
                response = client.post("/v1/academic/answers", json=payload)
                self.assertEqual(200, response.status_code)
                body = response.json()
                self.assertEqual("out_of_scope", body["status"])
                self.assertEqual(SANITIZED_DEPARTMENT, body["evidence_packet"]["scope"]["department"])
                self.assertNotIn(department, response.text)

    def test_api_registry_unavailable_is_generic_schema_body(self) -> None:
        client = TestClient(app)
        from academic_assistant.api import _engine
        _engine.cache_clear()
        with patch("academic_assistant.api.Registry.load", side_effect=RegistryUnavailable()):
            response = client.post("/v1/academic/answers", json=request("졸업학점", department=r"C:\\private\\student.txt").model_dump(mode="json"))
        _engine.cache_clear()
        self.assertEqual(503, response.status_code)
        self.assertEqual("insufficient_evidence", response.json()["status"])
        self.assertNotIn(str(ROOT), response.text)
        self.assertNotIn("private", response.text)
        self.assertEqual(SANITIZED_DEPARTMENT, response.json()["evidence_packet"]["scope"]["department"])

    def test_cli_and_core_canonical_json_match(self) -> None:
        expected = canonical_response_json(self.engine.answer(request("졸업학점")))
        command = [sys.executable, "-m", "academic_assistant", "ask", "--question", "졸업학점", "--admission-year", "2026", "--curriculum-year", "2026", "--department", "컴퓨터공학과", "--json"]
        completed = subprocess.run(command, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(expected, completed.stdout.strip())

    def test_cli_documented_year_and_credits_contract(self) -> None:
        command = [
            sys.executable, "-m", "academic_assistant", "ask",
            "--year", "2026",
            "--department", "컴퓨터공학과",
            "--question", "졸업학점 얼마나 부족해",
            "--credits", "credits.graduation.total=120",
            "--json",
        ]
        environment = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
        completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("supported", result["status"])
        self.assertEqual(2026, result["evidence_packet"]["scope"]["admission_year"])
        self.assertEqual(2026, result["evidence_packet"]["scope"]["matched_curriculum_year"])
        self.assertEqual(10, result["calculations"][0]["gap"])

        repeated = [
            sys.executable, "-m", "academic_assistant", "ask", "--year", "2026",
            "--department", "컴퓨터공학과", "--question", "전공필수 및 전공선택 얼마나 부족해",
            "--credits", "credits.major.required=20", "--credits", "credits.major.elective=22", "--json",
        ]
        completed = subprocess.run(repeated, cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(2, len(json.loads(completed.stdout)["calculations"]))

        conflict = [*command[:-1], "--admission-year", "2025", "--json"]
        completed = subprocess.run(conflict, cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(64, completed.returncode)
        self.assertEqual("invalid request", completed.stderr.strip())
        self.assertNotIn("2025", completed.stderr)

        help_result = subprocess.run([sys.executable, "-m", "academic_assistant", "ask", "--help"], cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, help_result.returncode)
        self.assertIn("--year", help_result.stdout)
        self.assertIn("--credits", help_result.stdout)

    def test_corrupt_registry_fails_without_path_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            (root / "contracts" / "rule-fact.schema.json").write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(RegistryUnavailable, "^academic registry unavailable$"):
                Registry.load(root)


if __name__ == "__main__":
    unittest.main()
