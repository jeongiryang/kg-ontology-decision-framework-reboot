import contextlib
import io
import json
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from academic_assistant import api, cli
from academic_assistant.core import AnswerEngine, canonical_response_json
from academic_assistant.models import AcademicAnswerRequest
from scripts.validation.validate_reference_patterns import validate

ROOT = Path(__file__).resolve().parents[1]


class ReferencePatternTests(unittest.TestCase):
    def test_fresh_patterns_and_exact_api_cli_parity(self):
        self.assertEqual([], validate(ROOT))
        payload = json.loads((ROOT / "evaluations/academic-reference-patterns.json").read_text(encoding="utf-8"))
        client = TestClient(api.app)
        for case in payload["cases"]:
            request = AcademicAnswerRequest(**payload["scope"], question=case["question"], earned_credits=case.get("earned_credits", {}))
            expected = canonical_response_json(AnswerEngine().answer(request))
            response = client.post("/v1/academic/answers", json=request.model_dump(mode="json"))
            self.assertEqual(200, response.status_code)
            self.assertEqual(json.loads(expected), response.json())
            args = ["ask", "--question", case["question"], "--year", "2026", "--department", "컴퓨터공학과", "--json"]
            for key, value in case.get("earned_credits", {}).items(): args += ["--credits", f"{key}={value}"]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(args)
            self.assertIn(code, (0, 2))
            self.assertEqual(json.loads(expected), json.loads(out.getvalue()))
