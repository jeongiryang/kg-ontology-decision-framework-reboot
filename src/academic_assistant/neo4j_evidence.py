"""Read-only, exact Registry-bound Neo4j evidence mirror (never a rule authority)."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from .kg import NODE_TYPES, build_graph, validate_graph
from .registry import Registry, RegistryUnavailable, canonical_bytes


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def _decode(properties: dict[str, Any]) -> dict[str, Any]:
    decoded = {}
    for key, value in properties.items():
        target = key[:-5] if key.endswith("_json") else key
        if target in decoded:
            raise ValueError("duplicate encoded property")
        if key.endswith("_json"):
            if not isinstance(value, str):
                raise ValueError("invalid encoded property")
            value = json.loads(value, object_pairs_hook=_unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
            if value is not None and not isinstance(value, (dict, list)):
                raise ValueError("encoded property is not structured")
        decoded[target] = value
    return decoded


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str
    database: str
    username: str
    password: str
    timeout_seconds: float = 3.0

    def __post_init__(self):
        parsed = urlsplit(self.uri)
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
            port = parsed.port
        except ValueError:
            raise ValueError("invalid graph configuration") from None
        if (parsed.scheme not in {"bolt", "bolt+s", "neo4j", "neo4j+s"}
                or not address.is_loopback or not port or parsed.path not in {"", "/"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", self.database)
                or not self.username or not self.password
                or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 10):
            raise ValueError("invalid graph configuration")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ
        backend = env.get("ACADEMIC_EVIDENCE_BACKEND", "registry").strip().lower()
        if backend == "registry":
            return None
        if backend != "neo4j":
            raise ValueError("invalid evidence backend")
        return cls(env.get("NEO4J_URI", ""), env.get("NEO4J_DATABASE", ""),
                   env.get("NEO4J_USER", ""), env.get("NEO4J_PASSWORD", ""),
                   float(env.get("ACADEMIC_NEO4J_TIMEOUT_SECONDS", "3")))


class Neo4jEvidenceReader:
    def __init__(self, settings: Neo4jSettings, *, driver=None):
        self.settings = settings
        try:
            if driver is None:
                from neo4j import GraphDatabase
                driver = GraphDatabase.driver(settings.uri, auth=(settings.username, settings.password),
                    connection_timeout=settings.timeout_seconds,
                    connection_acquisition_timeout=settings.timeout_seconds,
                    max_transaction_retry_time=0, max_connection_pool_size=2)
            self._driver = driver
        except Exception:
            raise RegistryUnavailable() from None

    @classmethod
    def from_env(cls, environ=None):
        try:
            settings = Neo4jSettings.from_env(environ)
            return cls(settings) if settings else None
        except Exception:
            raise RegistryUnavailable() from None

    def close(self):
        self._driver.close()

    def verify(self, registry: Registry):
        self._read_graph(registry)

    def _read_graph(self, registry: Registry) -> dict[str, Any]:
        expected = build_graph(registry)
        # One bounded query in one read transaction; no startup-only trust cache.
        query = """CALL () { MATCH (n) RETURN n LIMIT $node_limit }
        WITH collect({labels: labels(n), properties: properties(n)}) AS nodes
        CALL () { MATCH (a)-[r]->(b) RETURN a, r, b LIMIT $edge_limit }
        RETURN nodes, collect({from_id: a.node_id, to_id: b.node_id,
            type: type(r), properties: properties(r)}) AS relationships"""
        try:
            from neo4j import unit_of_work
            with self._driver.session(database=self.settings.database, default_access_mode="READ") as session:
                @unit_of_work(timeout=self.settings.timeout_seconds)
                def read(tx):
                    return tx.run(query,
                        node_limit=len(expected["nodes"]) + 1,
                        edge_limit=len(expected["relationships"]) + 1).single(strict=True).data()
                raw = session.execute_read(read)
            if len(canonical_bytes(raw)) > 1048576:
                raise ValueError("graph response exceeds bound")
            nodes = []
            for item in raw["nodes"]:
                labels = item["labels"]
                types = set(labels) - {"AcademicKG"}
                if len(labels) != 2 or "AcademicKG" not in labels or len(types) != 1 or not types <= NODE_TYPES:
                    raise ValueError("invalid graph labels")
                properties = dict(item["properties"])
                node_id = properties.pop("node_id")
                if properties.pop("graph_sha256") != expected["graph_sha256"]:
                    raise ValueError("stale graph")
                nodes.append({"id": node_id, "type": next(iter(types)), "properties": _decode(properties)})
            relationships = [{**edge, "properties": _decode(edge["properties"])} for edge in raw["relationships"]]
            nodes.sort(key=lambda item: item["id"])
            relationships.sort(key=lambda item: (item["from_id"], item["type"], item["to_id"], canonical_bytes(item["properties"])))
            graph = {**expected, "nodes": nodes, "relationships": relationships}
            validate_graph(graph, registry)
            return graph
        except Exception:
            raise RegistryUnavailable() from None

    def fetch_evidence(self, rule_ids: list[str], registry: Registry) -> list[dict[str, str]]:
        graph = self._read_graph(registry)
        by_id = {node["id"]: node["properties"] for node in graph["nodes"]}
        evidence = []
        try:
            for rule_id in rule_ids:
                rule = registry.rules[rule_id]
                for index in range(len(rule["evidence"])):
                    item = by_id[f"evidence:{rule_id}:{index:02d}"]
                    evidence.append({"rule_id": rule_id, "source_id": item["source_id"],
                        "locator": item["locator"], "claim": by_id[f"rule:{rule_id}"]["statement"]})
        except (KeyError, TypeError):
            raise RegistryUnavailable() from None
        return evidence

    def fetch_courses(self, registry: Registry) -> list[dict]:
        """Read real CourseFact nodes, then require the exact source-bound mirror."""
        graph = self._read_graph(registry)
        return [node["properties"] for node in graph["nodes"] if node["type"] == "CourseFact"]
