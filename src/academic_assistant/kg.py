"""Deterministic graph projection of the approved academic registry.

This module does not infer new rules.  A graph bundle is only a different,
traceable representation of the Registry that already passed pin and review
validation.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from .registry import Registry, canonical_bytes, canonical_sha256

GRAPH_SCHEMA_VERSION = "1.0.0"
NODE_TYPES = frozenset({"Cohort", "SourceEntry", "RuleFact", "Evidence", "CourseFact"})
RELATIONSHIP_TYPES = frozenset({"APPLIES_TO", "CITES", "FROM_SOURCE", "RELATES_TO"})
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class GraphValidationError(ValueError):
    """The bundle cannot be trusted as a projection of approved rules."""


def _require_approved(value: dict[str, Any], kind: str) -> None:
    review = value.get("review", {})
    if (review.get("status"), review.get("mode"), review.get("scope")) != (
        "approved", "human", "full"
    ):
        raise GraphValidationError(f"unapproved {kind} in registry")


def _project(registry: Registry) -> dict[str, Any]:
    if not isinstance(registry, Registry) or not _DIGEST.fullmatch(registry.digest):
        raise GraphValidationError("invalid registry")

    cohort_id = "cohort:2026:computer-engineering"
    nodes: list[dict[str, Any]] = [
        {
            "id": cohort_id,
            "type": "Cohort",
            "properties": {
                "admission_year": 2026,
                "curriculum_year": 2026,
                "department": "컴퓨터공학과",
            },
        }
    ]
    relationships: list[dict[str, Any]] = []

    for source_id, source in sorted(registry.sources.items()):
        _require_approved(source, "source")
        if source.get("source_id") != source_id or not _DIGEST.fullmatch(source.get("sha256", "")):
            raise GraphValidationError("invalid source identity")
        nodes.append(
            {
                "id": f"source:{source_id}",
                "type": "SourceEntry",
                "properties": {
                    "source_id": source_id,
                    "title": source["title"],
                    "sha256": source["sha256"],
                    "authority": source["authority"],
                    "applicability": copy.deepcopy(source["applicability"]),
                    "review_status": "approved",
                },
            }
        )

    for rule_id, rule in sorted(registry.rules.items()):
        _require_approved(rule, "rule")
        if rule.get("rule_id") != rule_id:
            raise GraphValidationError("invalid rule identity")
        rule_hash = canonical_sha256(rule)
        if registry.rule_hashes.get(rule_id) != rule_hash:
            raise GraphValidationError("rule hash mismatch")
        scope = rule.get("applicability", {})
        if (
            scope.get("admission_years") != [2026]
            or scope.get("curriculum_years") != [2026]
            or scope.get("departments") != ["컴퓨터공학과"]
        ):
            raise GraphValidationError("rule outside graph cohort")
        if not any(
            registry.sources.get(item["source_id"], {}).get("authority") != "validation_aid"
            for item in rule["evidence"]
            if item["source_id"] in registry.sources
        ):
            raise GraphValidationError("validation aid cannot be sole rule evidence")
        rule_node_id = f"rule:{rule_id}"
        answer_policy = rule.get("answer_policy", "policy_statement")
        nodes.append(
            {
                "id": rule_node_id,
                "type": "RuleFact",
                "properties": {
                    "rule_id": rule_id,
                    "rule_sha256": rule_hash,
                    "label": rule.get("label", rule_id),
                    "statement": rule["decision"]["statement"],
                    "outcome": copy.deepcopy(rule["decision"]["outcome"]),
                    "operator": rule["decision"]["operator"],
                    "applicability": copy.deepcopy(scope),
                    "relationship_kind": rule["relationship"]["kind"],
                    "answer_policy": answer_policy,
                    "answer_eligible": answer_policy != "record_only",
                    "review_status": "approved",
                },
            }
        )
        relationships.append(
            {"from_id": rule_node_id, "to_id": cohort_id, "type": "APPLIES_TO", "properties": {}}
        )

        for index, evidence in enumerate(rule["evidence"]):
            source_id = evidence["source_id"]
            source = registry.sources.get(source_id)
            if source is None:
                raise GraphValidationError("evidence refers to missing source")
            evidence_id = f"evidence:{rule_id}:{index:02d}"
            nodes.append(
                {
                    "id": evidence_id,
                    "type": "Evidence",
                    "properties": {
                        "rule_id": rule_id,
                        "rule_sha256": rule_hash,
                        "source_id": source_id,
                        "source_sha256": source["sha256"],
                        "locator": evidence["locator"],
                        "evidence_type": evidence["evidence_type"],
                        "excerpt": evidence.get("excerpt", ""),
                        "evidence_sha256": canonical_sha256(evidence),
                    },
                }
            )
            relationships.extend(
                (
                    {"from_id": rule_node_id, "to_id": evidence_id, "type": "CITES", "properties": {}},
                    {"from_id": evidence_id, "to_id": f"source:{source_id}", "type": "FROM_SOURCE", "properties": {}},
                )
            )

        for target in rule["relationship"]["target_rule_ids"]:
            if target not in registry.rules:
                raise GraphValidationError("rule relation refers to missing rule")
            relationships.append(
                {
                    "from_id": rule_node_id,
                    "to_id": f"rule:{target}",
                    "type": "RELATES_TO",
                    "properties": {"kind": rule["relationship"]["kind"]},
                }
            )

    if registry.catalogue is not None:
        source_id = registry.catalogue["source_id"]
        _require_approved(registry.sources[source_id], "catalogue source")
        for fact in registry.catalogue["courses"]:
            if fact["source_sha256"] != registry.sources[source_id]["sha256"] or fact["fact_sha256"] != canonical_sha256({k: v for k, v in fact.items() if k != "fact_sha256"}):
                raise GraphValidationError("invalid catalogue fact")
            node_id = "course:" + fact["course_id"]
            nodes.append({"id": node_id, "type": "CourseFact", "properties": copy.deepcopy(fact)})
            relationships.extend([
                {"from_id": node_id, "to_id": cohort_id, "type": "APPLIES_TO", "properties": {}},
                {"from_id": node_id, "to_id": f"source:{source_id}", "type": "FROM_SOURCE", "properties": {}},
            ])
    nodes.sort(key=lambda item: item["id"])
    relationships.sort(
        key=lambda item: (item["from_id"], item["type"], item["to_id"], canonical_bytes(item["properties"]))
    )
    payload = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "registry_digest": registry.digest,
        "nodes": nodes,
        "relationships": relationships,
    }
    payload["graph_sha256"] = canonical_sha256(payload)
    return payload


def _validate_shape(graph: dict[str, Any]) -> None:
    if not isinstance(graph, dict) or set(graph) != {
        "schema_version", "registry_digest", "nodes", "relationships", "graph_sha256"
    }:
        raise GraphValidationError("invalid graph shape")
    if graph["schema_version"] != GRAPH_SCHEMA_VERSION:
        raise GraphValidationError("unsupported graph schema")
    if not isinstance(graph["registry_digest"], str) or not _DIGEST.fullmatch(graph["registry_digest"]):
        raise GraphValidationError("invalid registry digest")
    graph_hash = graph["graph_sha256"]
    if not isinstance(graph_hash, str) or graph_hash != canonical_sha256(
        {key: value for key, value in graph.items() if key != "graph_sha256"}
    ):
        raise GraphValidationError("graph digest mismatch")
    if not isinstance(graph["nodes"], list) or not isinstance(graph["relationships"], list):
        raise GraphValidationError("invalid graph arrays")
    node_ids: set[str] = set()
    for node in graph["nodes"]:
        if not isinstance(node, dict) or set(node) != {"id", "type", "properties"}:
            raise GraphValidationError("invalid graph node")
        if (
            not isinstance(node["id"], str)
            or node["id"] in node_ids
            or not isinstance(node["type"], str)
            or node["type"] not in NODE_TYPES
        ):
            raise GraphValidationError("duplicate or invalid graph node")
        if not isinstance(node["properties"], dict):
            raise GraphValidationError("invalid graph node properties")
        node_ids.add(node["id"])
    if not node_ids:
        raise GraphValidationError("empty graph")
    edge_ids: set[tuple[str, str, str, bytes]] = set()
    for edge in graph["relationships"]:
        if not isinstance(edge, dict) or set(edge) != {"from_id", "to_id", "type", "properties"}:
            raise GraphValidationError("invalid graph relation")
        if (
            not isinstance(edge["from_id"], str)
            or not isinstance(edge["to_id"], str)
            or edge["from_id"] not in node_ids
            or edge["to_id"] not in node_ids
            or not isinstance(edge["type"], str)
            or edge["type"] not in RELATIONSHIP_TYPES
        ):
            raise GraphValidationError("dangling or invalid graph relation")
        if not isinstance(edge["properties"], dict):
            raise GraphValidationError("invalid graph relation properties")
        identity = (edge["from_id"], edge["to_id"], edge["type"], canonical_bytes(edge["properties"]))
        if identity in edge_ids:
            raise GraphValidationError("duplicate graph relation")
        edge_ids.add(identity)


def build_graph(registry: Registry) -> dict[str, Any]:
    """Project the current approved Registry into a canonical graph bundle."""
    graph = _project(registry)
    _validate_shape(graph)
    return graph


def validate_graph(graph: dict[str, Any], registry: Registry) -> None:
    """Require both structural integrity and an exact approved Registry match."""
    _validate_shape(graph)
    if canonical_bytes(graph) != canonical_bytes(_project(registry)):
        raise GraphValidationError("graph does not match approved registry")


def write_graph_bundle(
    graph: dict[str, Any], output_path: Path | str, *, registry: Registry | None = None
) -> Path:
    """Write canonical UTF-8 JSON after validation, returning the output path."""
    if registry is None:
        _validate_shape(graph)
    else:
        validate_graph(graph, registry)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(graph) + b"\n")
    return path


def read_graph_bundle(path: Path | str, registry: Registry) -> dict[str, Any]:
    """Read an exported bundle only when it still matches the approved registry."""
    try:
        graph = json.loads(Path(path).read_text(encoding="utf-8"))
        validate_graph(graph, registry)
    except (OSError, UnicodeError, json.JSONDecodeError, GraphValidationError) as exc:
        raise GraphValidationError("invalid graph bundle") from exc
    return graph
