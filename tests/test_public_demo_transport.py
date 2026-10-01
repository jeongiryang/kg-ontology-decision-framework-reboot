"""Real owned launcher/TCP checks; no tunnel, graph, model or private records."""
from __future__ import annotations

from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest

from scripts.operations import public_demo as demo
from academic_assistant.registry import Registry, canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
STARTUP_SECONDS = 20
SHUTDOWN_SECONDS = 5
MAX_RESPONSE_BYTES = 512 * 1024


def environment(private_root: Path, *, public=True):
    # Keep only platform bootstrap fields and explicitly trusted dependencies.
    # In particular there are no inherited model/Neo4j settings or credentials.
    result = {key: value for key, value in os.environ.items()
              if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"}}
    paths = [str(ROOT / "src")]
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        paths.append(str(Path(sys.prefix) / "Lib" / "site-packages"))
    result.update({"PYTHONPATH": os.pathsep.join(paths), "PYTHONUNBUFFERED": "1",
                   "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1",
                   "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(private_root),
                   "ACADEMIC_FEEDBACK_PATH": str(private_root / "feedback.jsonl")})
    if public:
        result["ACADEMIC_PUBLIC_DEMO"] = "1"
    return result


def question(text="졸업학점은 얼마인가요?", **updates):
    value = {"question": text, "admission_year": 2026,
             "matched_curriculum_year": 2026, "department": "컴퓨터공학과",
             "earned_credits": {}}
    value.update(updates)
    return value


class PublicDemoNativeTransportTests(unittest.TestCase):
    def request(self, path, *, method="GET", payload=None, headers=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        values = dict(headers or {})
        if body is not None:
            values.setdefault("Content-Type", "application/json")
        with closing(http.client.HTTPConnection(demo.HOST, demo.PORT, timeout=3)) as connection:
            connection.request(method, path, body=body, headers=values)
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
            self.assertLessEqual(len(data), MAX_RESPONSE_BYTES)
            return response.status, {key.lower(): value for key, value in response.getheaders()}, data

    def assert_json(self, path, expected_status=200, **kwargs):
        status, headers, body = self.request(path, **kwargs)
        self.assertEqual(expected_status, status, (path, status, body[:200]))
        self.assertEqual("no-store", headers["cache-control"])
        self.assertNotIn("set-cookie", headers)
        self.assertNotIn(str(ROOT).encode("utf-8"), body)
        return json.loads(body)

    def wait_ready(self, owned):
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            self.assertIsNone(owned.process.poll(), "owned native web exited before readiness")
            if demo.local_ready(min(1, deadline - time.monotonic())):
                return
            time.sleep(.1)
        self.fail("owned native TCP readiness exceeded bounded startup")

    def assert_unrelated_listener(self, listener):
        with closing(socket.create_connection(listener.getsockname(), timeout=1)):
            accepted, _ = listener.accept()
            accepted.close()

    def test_actual_owned_native_tcp_public_api_and_clean_shutdown(self):
        self.assertTrue(demo.port_available(), "8765 is occupied; no existing owner will be touched")
        registry = Registry.load(ROOT)
        rule_id = "cwnu.cs.2026.credits.graduation-total"
        rule = registry.rules[rule_id]
        owned = None
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="public-native-transport-") as directory, \
                closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as unrelated:
            private_root = Path(directory)
            unrelated.bind(("127.0.0.1", 0))
            unrelated.listen(1)
            unrelated.settimeout(1)
            self.assert_unrelated_listener(unrelated)
            try:
                owned = demo.spawn_web(environment(private_root), SHUTDOWN_SECONDS)
                if os.name == "nt":
                    self.assertIsNotNone(owned.job)
                    self.assertTrue(owned.job.handle)
                self.wait_ready(owned)
                self.assertLess(time.monotonic() - started, STARTUP_SECONDS)
                self.assertEqual({"status": "ready", "public_demo": True}, self.assert_json("/readyz"))
                marker = self.assert_json("/v1/academic/demo")
                self.assertEqual({"public": True, "authentication": False,
                                  "transcript_storage": "request_memory",
                                  "provider": "cloudflare_quick_tunnel"}, marker)
                answer = self.assert_json("/v1/academic/chat", method="POST", payload=question())
                self.assertEqual("supported", answer["status"])
                self.assertIn(rule["decision"]["statement"], answer["answer"])
                packet = answer["evidence_packet"]
                self.assertEqual("supported", packet["status"])
                self.assertEqual({"admission_year": 2026, "matched_curriculum_year": 2026,
                                  "department": "컴퓨터공학과"}, packet["scope"])
                self.assertEqual([{ "rule_id": rule_id, "rule_sha256": canonical_sha256(rule)}],
                                 packet["applied_rules"])
                self.assertEqual([], packet["issues"])
                self.assertEqual([{ "source_id": entry["source_id"], "rule_id": rule_id,
                                    "locator": entry["locator"], "claim": rule["decision"]["statement"]}
                                  for entry in rule["evidence"]], packet["evidence"])
                metric = "credits.graduation.total"
                for credits, gap in ((100, 30), (120, 10)):
                    result = self.assert_json("/v1/academic/chat", method="POST", payload=question(
                        "졸업학점 얼마나 부족해", earned_credits={metric: credits}))
                    self.assertEqual([{ "metric": metric, "required": 130,
                                      "earned": credits, "gap": gap}], result["calculations"])
                absent = self.assert_json("/v1/academic/chat", method="POST",
                                          payload=question("졸업학점 얼마나 부족해"))
                self.assertEqual("insufficient_evidence", absent["status"])
                self.assertEqual({}, absent["evidence_packet"]["student_facts"])
                for path in ("/docs", "/openapi.json", "/.local/prototype.json", "/v1/models"):
                    self.assert_json(path, 404)
                self.assert_json("/v1/academic/feedback", 404, method="POST", payload={"question": "휴학 절차"})
                unsafe = question("내 학번 2026123456 기준 졸업학점은?")
                self.assertEqual({"detail": "invalid request"},
                                 self.assert_json("/v1/academic/chat", 422, method="POST", payload=unsafe))
                self.assert_json("/v1/academic/chat", 403, method="POST", payload=question(),
                                 headers={"Origin": "https://untrusted.invalid"})
                inventory = self.assert_json("/v1/academic/runtime")
                self.assertEqual("registry", inventory["evidence_backend"])
                self.assertFalse(inventory["graph_verified"])
                self.assertFalse(inventory["llm_configured"])
                self.assertFalse(inventory["llm_model_available"])
                self.assertFalse(inventory["student_records_sent_to_llm"])
                self.assertEqual([], list(private_root.iterdir()))
                self.assert_unrelated_listener(unrelated)
            finally:
                if owned is not None:
                    self.assertTrue(owned.stop(SHUTDOWN_SECONDS), "owned web cleanup failed")
            self.assertIsNotNone(owned)
            self.assertEqual(0, owned.process.returncode, "stdin shutdown did not exit cleanly")
            self.assertTrue(owned.process.stdin.closed)
            if os.name == "nt":
                self.assertIsNone(owned.job.handle)
            self.assertTrue(demo.port_available(), "owned native listener remained after shutdown")
            self.assert_unrelated_listener(unrelated)

    def test_actual_native_wrapper_requires_explicit_public_profile(self):
        self.assertTrue(demo.port_available(), "8765 is occupied; no existing owner will be touched")
        owned = None
        with tempfile.TemporaryDirectory(prefix="public-native-profile-") as directory:
            try:
                owned = demo.spawn_web(environment(Path(directory), public=False), SHUTDOWN_SECONDS)
                self.assertEqual(demo.EXIT_CONFIG, owned.process.wait(timeout=SHUTDOWN_SECONDS))
                self.assertTrue(demo.port_available(), "missing public flag opened a listener")
                self.assertEqual([], list(Path(directory).iterdir()))
            finally:
                if owned is not None:
                    self.assertTrue(owned.stop(SHUTDOWN_SECONDS))


if __name__ == "__main__":
    unittest.main()
