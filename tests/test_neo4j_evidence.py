from __future__ import annotations

import copy
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from academic_assistant import api
from academic_assistant.core import AnswerEngine, canonical_response_json
from academic_assistant.kg import build_graph
from academic_assistant.models import AcademicAnswerRequest
from academic_assistant.neo4j_evidence import Neo4jEvidenceReader, Neo4jSettings
from academic_assistant.registry import Registry, RegistryUnavailable
from fastapi.testclient import TestClient
from scripts.knowledge.build_graph import _neo4j_properties


def stored_graph(registry):
    graph = build_graph(registry)
    return {"nodes": [{"labels": ["AcademicKG", n["type"]], "properties": {
        "node_id": n["id"], "graph_sha256": graph["graph_sha256"], **_neo4j_properties(n["properties"])
    }} for n in graph["nodes"]], "relationships": copy.deepcopy(graph["relationships"])}


class Driver:
    def __init__(self, raw):
        self.raw = raw
        self.closed = False
        self.calls = 0

    def session(self, **kwargs):
        assert kwargs["default_access_mode"] == "READ"
        return self

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute_read(self, operation): return operation(self)
    def run(self, query, **params):
        self.calls += 1
        return self
    def single(self, **kwargs): return self
    def data(self): return copy.deepcopy(self.raw)
    def close(self): self.closed = True


class Neo4jEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.registry = Registry.load()

    def reader(self, raw=None):
        self.driver = Driver(stored_graph(self.registry) if raw is None else raw)
        return Neo4jEvidenceReader(Neo4jSettings("bolt://127.0.0.1:7687", "neo4j", "test", "test"), driver=self.driver)

    def test_valid_projection_preserves_answers_including_record_only_policy(self):
        reader = self.reader()
        engine = AnswerEngine(self.registry, evidence_reader=reader)
        for question in ("졸업학점 기준", "전공필수 과목 목록", "심층상담", "학석사연계과정 논문 면제 가능성이 있나요?"):
            request = AcademicAnswerRequest(question=question, admission_year=2026, matched_curriculum_year=2026, department="컴퓨터공학과")
            expected = AnswerEngine(self.registry).answer(request)
            self.assertEqual("supported", expected.status)
            self.assertEqual(canonical_response_json(expected), canonical_response_json(engine.answer(request)))
        self.assertEqual(4, self.driver.calls)
        reader.close()
        self.assertTrue(self.driver.closed)

    def test_graph_is_revalidated_each_time(self):
        reader = self.reader()
        reader.verify(self.registry)
        self.driver.raw["nodes"][0]["properties"]["department"] = "다른학과"
        with self.assertRaises(RegistryUnavailable): reader.verify(self.registry)

    def test_tampering_duplicates_missing_edges_and_unknown_properties_rejected(self):
        raw = stored_graph(self.registry)
        variants = []
        changed = copy.deepcopy(raw); changed["nodes"][0]["labels"].append("Foreign"); variants.append(changed)
        changed = copy.deepcopy(raw); changed["nodes"][0]["properties"]["extra"] = "untrusted"; variants.append(changed)
        changed = copy.deepcopy(raw); changed["nodes"][0]["properties"]["graph_sha256"] = "0" * 64; variants.append(changed)
        changed = copy.deepcopy(raw); changed["nodes"].append(changed["nodes"][0]); variants.append(changed)
        changed = copy.deepcopy(raw); changed["nodes"].pop(); variants.append(changed)
        changed = copy.deepcopy(raw); changed["relationships"].pop(); variants.append(changed)
        changed = copy.deepcopy(raw); changed["relationships"].append(changed["relationships"][0]); variants.append(changed)
        changed = copy.deepcopy(raw)
        for node in changed["nodes"]:
            if "outcome_json" in node["properties"]:
                node["properties"]["outcome_json"] = '{"credits":130,"credits":1}'
                break
        variants.append(changed)
        for altered in variants:
            with self.subTest(altered=variants.index(altered)), self.assertRaises(RegistryUnavailable):
                self.reader(altered).verify(self.registry)

    def test_environment_strict_and_default_off(self):
        self.assertIsNone(Neo4jEvidenceReader.from_env({}))
        for env in ({"ACADEMIC_EVIDENCE_BACKEND":"unknown"}, {"ACADEMIC_EVIDENCE_BACKEND":"neo4j"}):
            with self.assertRaises(RegistryUnavailable): Neo4jEvidenceReader.from_env(env)
        for uri in ("bolt://example.com:7687", "bolt://localhost:7687", "bolt://127.0.0.1:7687/x"):
            with self.assertRaises(ValueError): Neo4jSettings(uri, "neo4j", "u", "p")
        with self.assertRaises(ValueError): Neo4jSettings("bolt://127.0.0.1:7687", "neo4j", "u", "p", float("nan"))

    def test_api_graph_failure_is_sanitized_503(self):
        reader = self.reader({"nodes": [], "relationships": []})
        engine = AnswerEngine(self.registry, evidence_reader=reader)
        with patch.object(api, "_engine", return_value=engine):
            client = TestClient(api.app)
            self.assertEqual(503, client.get("/readyz").status_code)
            response = client.post("/v1/academic/answers", json={"question":"졸업학점", "admission_year":2026, "matched_curriculum_year":2026, "department":"컴퓨터공학과"})
            self.assertEqual(503, response.status_code)
            self.assertNotEqual("supported", response.json()["status"])
            self.assertNotIn("bolt", response.text)

    def test_concurrent_cold_start_owns_one_reader_and_shutdown_closes_it(self):
        api._chat_engine.cache_clear(); api._engine.cache_clear()
        reader = self.reader()
        entered, release = threading.Event(), threading.Event()
        def create(): entered.set(); release.wait(2); return reader
        with patch.object(api.Neo4jEvidenceReader, "from_env", side_effect=create) as factory:
            with TestClient(api.app):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(api._engine)
                    self.assertTrue(entered.wait(1))
                    second = pool.submit(api._engine)
                    release.set()
                    self.assertIs(first.result(2), second.result(2))
                self.assertEqual(1, factory.call_count)
            self.assertTrue(self.driver.closed)
            self.assertEqual(0, api._engine.cache_info().currsize)

    def test_shutdown_close_failure_still_clears_cache(self):
        api._chat_engine.cache_clear(); api._engine.cache_clear()
        reader = self.reader()
        with patch.object(api.Neo4jEvidenceReader, "from_env", return_value=reader), patch.object(reader, "close", side_effect=RuntimeError("fixture")):
            with self.assertRaises(RuntimeError):
                with TestClient(api.app): api._engine()
        self.assertEqual(0, api._engine.cache_info().currsize)


if __name__ == "__main__": unittest.main()
