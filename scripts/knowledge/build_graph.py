"""Export approved academic rules as a local graph or explicitly load an empty Neo4j DB."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.kg import (  # noqa: E402
    NODE_TYPES,
    RELATIONSHIP_TYPES,
    GraphValidationError,
    build_graph,
    validate_graph,
    write_graph_bundle,
)
from academic_assistant.registry import Registry, RegistryUnavailable  # noqa: E402

DEFAULT_OUTPUT = ROOT / ".local" / "academic-kg" / "2026-computer-engineering.json"
_NEO4J_DATABASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _neo4j_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Encode nested, typed JSON for Neo4j property storage without losing content."""
    result: dict[str, Any] = {}
    for key, value in properties.items():
        if isinstance(value, (dict, list)):
            result[f"{key}_json"] = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        elif value is not None and isinstance(value, (str, int, float, bool)):
            result[key] = value
        else:
            raise GraphValidationError("unsupported graph property")
    return result


def load_empty_neo4j(
    graph: dict[str, Any],
    registry: Registry,
    *,
    uri: str,
    database: str,
    username: str,
    password: str,
) -> tuple[int, int]:
    """Load into an explicitly selected, empty database; never delete or sync."""
    validate_graph(graph, registry)
    if not uri.startswith(("neo4j://", "neo4j+s://", "bolt://", "bolt+s://")):
        raise GraphValidationError("unsupported Neo4j endpoint")
    if not _NEO4J_DATABASE.fullmatch(database) or not username or not password:
        raise GraphValidationError("incomplete Neo4j configuration")
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise GraphValidationError("Neo4j driver is not installed") from exc

    graph_hash = graph["graph_sha256"]

    def write_transaction(tx: Any) -> tuple[int, int]:
        count = tx.run("MATCH (n) RETURN count(n) AS count").single(strict=True)["count"]
        if count != 0:
            raise GraphValidationError("Neo4j database is not empty")
        for node in graph["nodes"]:
            label = node["type"]
            if label not in NODE_TYPES:
                raise GraphValidationError("invalid node label")
            tx.run(
                f"CREATE (n:AcademicKG:{label} {{node_id: $node_id, graph_sha256: $graph_sha256}}) "
                "SET n += $properties",
                node_id=node["id"],
                graph_sha256=graph_hash,
                properties=_neo4j_properties(node["properties"]),
            ).consume()
        for edge in graph["relationships"]:
            relation_type = edge["type"]
            if relation_type not in RELATIONSHIP_TYPES:
                raise GraphValidationError("invalid relationship type")
            tx.run(
                "MATCH (a:AcademicKG {node_id: $from_id, graph_sha256: $graph_sha256}) "
                "MATCH (b:AcademicKG {node_id: $to_id, graph_sha256: $graph_sha256}) "
                f"CREATE (a)-[r:{relation_type}]->(b) SET r += $properties",
                from_id=edge["from_id"],
                to_id=edge["to_id"],
                graph_sha256=graph_hash,
                properties=_neo4j_properties(edge["properties"]),
            ).consume()
        loaded_nodes = tx.run("MATCH (n:AcademicKG) RETURN count(n) AS count").single(strict=True)["count"]
        loaded_edges = tx.run(
            "MATCH (:AcademicKG)-[r]->(:AcademicKG) RETURN count(r) AS count"
        ).single(strict=True)["count"]
        if (loaded_nodes, loaded_edges) != (len(graph["nodes"]), len(graph["relationships"])):
            raise GraphValidationError("Neo4j load count mismatch")
        return loaded_nodes, loaded_edges

    try:
        with GraphDatabase.driver(uri, auth=(username, password), max_transaction_retry_time=0) as driver:
            driver.verify_connectivity()
            with driver.session(database=database) as session:
                # Do not even provision schema on an occupied database.
                count = session.run("MATCH (n) RETURN count(n) AS count").single(strict=True)["count"]
                if count:
                    raise GraphValidationError("Neo4j database is not empty")
                session.run("CREATE CONSTRAINT academic_kg_node_id IF NOT EXISTS "
                    "FOR (n:AcademicKG) REQUIRE n.node_id IS UNIQUE").consume()
                constraints = session.run("SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties "
                    "WHERE type = 'UNIQUENESS' AND labelsOrTypes = ['AcademicKG'] "
                    "AND properties = ['node_id'] RETURN count(*) AS count").single(strict=True)["count"]
                if not constraints:
                    raise GraphValidationError("Neo4j uniqueness constraint unavailable")
                # All loaders create the same unique cohort node in this atomic
                # transaction. Competing initial loads cannot both commit.
                return session.execute_write(write_transaction)
    except GraphValidationError:
        raise
    except Exception:
        # A network failure may mean the atomic commit was acknowledged late:
        # inspect the dedicated DB; never blindly delete or retry the load.
        raise GraphValidationError("Neo4j load failed; verify dedicated database before retry") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a graph from approved 2026 academic rules")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--load-neo4j", action="store_true")
    parser.add_argument("--neo4j-uri", help="Explicit Neo4j Bolt endpoint; no default")
    parser.add_argument("--neo4j-database", help="Explicit target database name")
    parser.add_argument("--confirm-empty-database", action="store_true")
    args = parser.parse_args(argv)
    try:
        registry = Registry.load(ROOT)
        graph = build_graph(registry)
        output = args.output.resolve()
        private_root = (ROOT / ".local" / "academic-kg").resolve()
        if not output.is_relative_to(private_root) or output == private_root:
            raise GraphValidationError("output must be under the private graph directory")
        write_graph_bundle(graph, output, registry=registry)
        result: dict[str, Any] = {
            "output": str(output),
            "graph_sha256": graph["graph_sha256"],
            "nodes": len(graph["nodes"]),
            "relationships": len(graph["relationships"]),
            "neo4j_loaded": False,
        }
        if args.load_neo4j:
            if not args.confirm_empty_database or not args.neo4j_uri or not args.neo4j_database:
                raise GraphValidationError("explicit empty-database confirmation and endpoint required")
            result["loaded_nodes"], result["loaded_relationships"] = load_empty_neo4j(
                graph,
                registry,
                uri=args.neo4j_uri,
                database=args.neo4j_database,
                username=os.environ.get("NEO4J_USER", ""),
                password=os.environ.get("NEO4J_PASSWORD", ""),
            )
            result["neo4j_loaded"] = True
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (GraphValidationError, RegistryUnavailable, OSError) as exc:
        print(f"graph build unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
