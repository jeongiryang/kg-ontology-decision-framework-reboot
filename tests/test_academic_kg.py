from __future__ import annotations

import json
import runpy
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.kg import (
    GraphValidationError,
    build_graph,
    read_graph_bundle,
    validate_graph,
    write_graph_bundle,
)
from academic_assistant.registry import Registry, canonical_sha256


class AcademicGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = Registry.load(ROOT)

    def test_projection_is_stable_and_traces_claim_to_pdf(self) -> None:
        graph = build_graph(self.registry)
        self.assertEqual(graph, build_graph(self.registry))
        self.assertEqual(self.registry.digest, graph["registry_digest"])
        self.assertEqual(26, sum(node["type"] == "RuleFact" for node in graph["nodes"]))
        self.assertEqual(2, sum(node["type"] == "SourceEntry" for node in graph["nodes"]))
        rule_id = "cwnu.cs.2026.credits.graduation-total"
        source_id = "cwnu.curriculum.2026.changwon-undergraduate"
        by_id = {node["id"]: node for node in graph["nodes"]}
        rule = by_id[f"rule:{rule_id}"]["properties"]
        evidence = by_id[f"evidence:{rule_id}:00"]["properties"]
        source = by_id[f"source:{source_id}"]["properties"]
        self.assertEqual(canonical_sha256(self.registry.rules[rule_id]), rule["rule_sha256"])
        self.assertEqual("approved", rule["review_status"])
        self.assertEqual(130, rule["outcome"]["credits"])
        self.assertIn("PDF p.23", evidence["locator"])
        self.assertEqual(rule["rule_sha256"], evidence["rule_sha256"])
        self.assertEqual(source["sha256"], evidence["source_sha256"])
        edges = {(edge["from_id"], edge["type"], edge["to_id"]) for edge in graph["relationships"]}
        self.assertIn((f"rule:{rule_id}", "CITES", f"evidence:{rule_id}:00"), edges)
        self.assertIn((f"evidence:{rule_id}:00", "FROM_SOURCE", f"source:{source_id}"), edges)
        self.assertIn((f"rule:{rule_id}", "APPLIES_TO", "cohort:2026:computer-engineering"), edges)

    def test_unverified_operational_claims_are_absent_and_record_only_is_flagged(self) -> None:
        graph = build_graph(self.registry)
        text = json.dumps(graph, ensure_ascii=False)
        self.assertNotIn("PCCP 400", text)
        self.assertNotIn("캡스톤디자인 I", text)
        self.assertNotIn("총장상급", text)
        self.assertNotIn("7a322574b35aa969", text)  # unverified regulation screenshot
        by_id = {node["id"]: node for node in graph["nodes"]}
        exception = by_id["rule:cwnu.cs.2026.graduation.thesis-linked-program-exemption"]["properties"]
        self.assertEqual("record_only", exception["answer_policy"])
        self.assertFalse(exception["answer_eligible"])

    def test_tamper_and_unapproved_registry_fail_closed(self) -> None:
        graph = build_graph(self.registry)
        graph["nodes"][0]["properties"]["department"] = "전자공학과"
        with self.assertRaises(GraphValidationError):
            validate_graph(graph, self.registry)

        source_id = next(iter(self.registry.sources))
        unapproved_source = json.loads(json.dumps(self.registry.sources[source_id]))
        unapproved_source["review"]["status"] = "needs_review"
        registry = replace(self.registry, sources={**self.registry.sources, source_id: unapproved_source})
        with self.assertRaises(GraphValidationError):
            build_graph(registry)

        rule_id = "cwnu.cs.2026.major-counseling-completion"
        aid_only = json.loads(json.dumps(self.registry.rules[rule_id]))
        aid_only["evidence"] = [
            item for item in aid_only["evidence"]
            if item["source_id"] == "cwnu.curriculum.2026.ta-validation-response"
        ]
        registry = replace(
            self.registry,
            rules={**self.registry.rules, rule_id: aid_only},
            rule_hashes={**self.registry.rule_hashes, rule_id: canonical_sha256(aid_only)},
        )
        with self.assertRaisesRegex(GraphValidationError, "validation aid"):
            build_graph(registry)

    def test_bundle_round_trip_and_registry_binding(self) -> None:
        graph = build_graph(self.registry)
        with tempfile.TemporaryDirectory() as directory:
            path = write_graph_bundle(graph, Path(directory) / "approved-graph.json", registry=self.registry)
            self.assertEqual(graph, read_graph_bundle(path, self.registry))
            self.assertEqual(canonical_sha256({key: value for key, value in graph.items() if key != "graph_sha256"}), graph["graph_sha256"])
            altered = json.loads(path.read_text(encoding="utf-8"))
            altered["registry_digest"] = "0" * 64
            path.write_text(json.dumps(altered, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(GraphValidationError):
                read_graph_bundle(path, self.registry)

    def test_neo4j_loader_refuses_nonempty_database_before_write(self) -> None:
        queries: list[str] = []

        class FakeResult:
            def single(self, *, strict: bool) -> dict[str, int]:
                return {"count": 1}

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def verify_connectivity(self) -> None:
                return None

            def session(self, *, database: str):
                return self

            def execute_write(self, operation):
                return operation(self)

            def run(self, query: str, **params):
                queries.append(query)
                return FakeResult()

        fake_module = types.SimpleNamespace(
            GraphDatabase=types.SimpleNamespace(driver=lambda *args, **kwargs: FakeConnection())
        )
        loader = runpy.run_path(str(ROOT / "scripts" / "knowledge" / "build_graph.py"), run_name="kg_test")["load_empty_neo4j"]
        with patch.dict(sys.modules, {"neo4j": fake_module}):
            with self.assertRaisesRegex(GraphValidationError, "database is not empty"):
                loader(build_graph(self.registry), self.registry, uri="bolt://localhost:7687", database="test", username="tester", password="secret")
        self.assertEqual(["MATCH (n) RETURN count(n) AS count"], queries)

    def test_neo4j_loader_writes_only_to_explicit_empty_database(self) -> None:
        graph = build_graph(self.registry)
        counts = {"nodes": 0, "relationships": 0}
        session_names: list[str] = []

        class FakeResult:
            def __init__(self, count: int | None = None) -> None:
                self.count = count

            def single(self, *, strict: bool) -> dict[str, int]:
                assert self.count is not None
                return {"count": self.count}

            def consume(self) -> None:
                return None

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def verify_connectivity(self) -> None:
                return None

            def session(self, *, database: str):
                session_names.append(database)
                return self

            def execute_write(self, operation):
                return operation(self)

            def run(self, query: str, **params):
                if query == "MATCH (n) RETURN count(n) AS count":
                    return FakeResult(counts["nodes"])
                if query.startswith("CREATE (n:AcademicKG:"):
                    counts["nodes"] += 1
                    return FakeResult()
                if "CREATE (a)-[r:" in query:
                    counts["relationships"] += 1
                    return FakeResult()
                if query.startswith("MATCH (n:AcademicKG) RETURN count(n)"):
                    return FakeResult(counts["nodes"])
                if query.startswith("MATCH (:AcademicKG)-[r]->(:AcademicKG)"):
                    return FakeResult(counts["relationships"])
                raise AssertionError(f"unexpected query: {query}")

        fake_module = types.SimpleNamespace(
            GraphDatabase=types.SimpleNamespace(driver=lambda *args, **kwargs: FakeConnection())
        )
        loader = runpy.run_path(str(ROOT / "scripts" / "knowledge" / "build_graph.py"), run_name="kg_test")["load_empty_neo4j"]
        with patch.dict(sys.modules, {"neo4j": fake_module}):
            loaded = loader(graph, self.registry, uri="bolt://localhost:7687", database="isolated_kg", username="tester", password="secret")
        self.assertEqual(["isolated_kg"], session_names)
        self.assertEqual((len(graph["nodes"]), len(graph["relationships"])), loaded)


if __name__ == "__main__":
    unittest.main()
