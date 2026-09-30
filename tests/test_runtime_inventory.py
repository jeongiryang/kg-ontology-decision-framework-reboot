from __future__ import annotations
import io
import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from academic_assistant import api
from academic_assistant.llm import LocalLLMClient, LLMSettings


class RuntimeInventoryTests(unittest.TestCase):
    def test_inventory_does_not_run_inference(self):
        for provider, payload, path in (
            ("ollama", {"models": [{"name": "synthetic-model"}]}, "/api/tags"),
            ("lmstudio", {"data": [{"id": "synthetic-model"}]}, "/v1/models"),
        ):
            client = LocalLLMClient(LLMSettings(provider, "http://127.0.0.1:19000", "synthetic-model"))
            with patch.object(client._opener, "open", return_value=io.BytesIO(json.dumps(payload).encode())) as opened:
                self.assertTrue(client.model_available())
                request = opened.call_args.args[0]
                self.assertEqual("GET", request.get_method())
                self.assertTrue(request.full_url.endswith(path))
                self.assertIsNone(request.data)

    def test_inventory_failed_closed(self):
        client = LocalLLMClient(LLMSettings("ollama", "http://127.0.0.1:19000", "synthetic-model"))
        for raw in (b"invalid", b"null", b'{"models":null}', b"x" * 32769):
            with patch.object(client._opener, "open", return_value=io.BytesIO(raw)):
                self.assertFalse(client.model_available())

    def test_runtime_exposes_no_endpoint_or_password(self):
        api._chat_engine.cache_clear()
        api._engine.cache_clear()
        with patch.dict("os.environ", {"ACADEMIC_EVIDENCE_BACKEND":"registry", "ACADEMIC_LLM_PROVIDER":"disabled"}):
            with TestClient(api.app) as client:
                response = client.get("/v1/academic/runtime")
                self.assertEqual(200, response.status_code)
                self.assertFalse(response.json()["llm_model_available"])
                self.assertFalse(response.json()["student_records_sent_to_llm"])
                self.assertNotIn("127.0.0.1", response.text)
                self.assertNotIn("password", response.text)


if __name__ == "__main__":
    unittest.main()
