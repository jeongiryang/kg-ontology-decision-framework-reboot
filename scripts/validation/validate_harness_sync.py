#!/usr/bin/env python3
"""Validate synchronization between the harness manifest and executable contracts."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml
from jsonschema import exceptions as jsonschema_exceptions
from jsonschema.validators import validator_for


DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


def _mapping(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    errors.append(f"{label}: expected a mapping")
    return {}


def _list(value: Any, label: str, errors: list[str]) -> list[Any]:
    if isinstance(value, list):
        return value
    errors.append(f"{label}: expected a list")
    return []


def _safe_path(project: Path, raw: Any, label: str, errors: list[str]) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        errors.append(f"{label}: expected a non-empty repository-relative path")
        return None
    candidate = (project / raw).resolve()
    try:
        candidate.relative_to(project)
    except ValueError:
        errors.append(f"{label}: path escapes project: {raw}")
        return None
    return candidate


def _load_yaml(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors.append(f"{path.name}: cannot load YAML: {exc}")
        return {}
    return _mapping(data, path.name, errors)


def _load_toml(path: Path, project: Path, errors: list[str]) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"{path.relative_to(project)}: cannot load TOML: {exc}")
        return {}
    return _mapping(data, str(path.relative_to(project)), errors)


def _skill_frontmatter(path: Path, project: Path, errors: list[str]) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.relative_to(project)}: cannot read skill: {exc}")
        return {}
    if not lines or lines[0].strip() != "---":
        errors.append(f"{path.relative_to(project)}: missing YAML frontmatter")
        return {}
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        errors.append(f"{path.relative_to(project)}: unclosed YAML frontmatter")
        return {}
    try:
        data = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        errors.append(f"{path.relative_to(project)}: invalid YAML frontmatter: {exc}")
        return {}
    return _mapping(data, str(path.relative_to(project)), errors)


def _duplicate_values(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _validate_agents(project: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    entries = _list(manifest.get("agents"), "agents", errors)
    declared_names: list[str] = []
    declared_paths: list[str] = []
    for index, raw_entry in enumerate(entries):
        entry = _mapping(raw_entry, f"agents[{index}]", errors)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"agents[{index}].name: expected a non-empty string")
            continue
        declared_names.append(name)
        raw_path = entry.get("config")
        if isinstance(raw_path, str):
            declared_paths.append(raw_path.replace("\\", "/"))
        path = _safe_path(project, raw_path, f"agents[{index}].config", errors)
        if path is None:
            continue
        if not path.is_file():
            errors.append(f"agents[{index}].config: missing file: {raw_path}")
            continue
        config = _load_toml(path, project, errors)
        if config.get("name") != name:
            errors.append(
                f"{path.relative_to(project)}: TOML name {config.get('name')!r} "
                f"does not match manifest name {name!r}"
            )
    for duplicate in _duplicate_values(declared_names):
        errors.append(f"agents: duplicate name: {duplicate}")
    for duplicate in _duplicate_values(declared_paths):
        errors.append(f"agents: duplicate config path: {duplicate}")

    actual_paths = {
        path.relative_to(project).as_posix()
        for path in (project / ".codex/agents").glob("*.toml")
        if path.is_file()
    }
    declared = set(declared_paths)
    for path in sorted(actual_paths - declared):
        errors.append(f"agents: unregistered TOML: {path}")
    for path in sorted(declared - actual_paths):
        errors.append(f"agents: manifest config has no TOML: {path}")


def _validate_skills(project: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    entries = _list(manifest.get("skills"), "skills", errors)
    declared_names: list[str] = []
    declared_paths: list[str] = []
    for index, raw_entry in enumerate(entries):
        entry = _mapping(raw_entry, f"skills[{index}]", errors)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"skills[{index}].name: expected a non-empty string")
            continue
        declared_names.append(name)
        raw_path = entry.get("path")
        if isinstance(raw_path, str):
            declared_paths.append(raw_path.replace("\\", "/"))
        path = _safe_path(project, raw_path, f"skills[{index}].path", errors)
        if path is None:
            continue
        if not path.is_file():
            errors.append(f"skills[{index}].path: missing file: {raw_path}")
            continue
        frontmatter = _skill_frontmatter(path, project, errors)
        if frontmatter.get("name") != name:
            errors.append(
                f"{path.relative_to(project)}: frontmatter name {frontmatter.get('name')!r} "
                f"does not match manifest name {name!r}"
            )
        if not isinstance(frontmatter.get("description"), str) or not frontmatter["description"].strip():
            errors.append(f"{path.relative_to(project)}: frontmatter description is required")
    for duplicate in _duplicate_values(declared_names):
        errors.append(f"skills: duplicate name: {duplicate}")
    for duplicate in _duplicate_values(declared_paths):
        errors.append(f"skills: duplicate path: {duplicate}")

    skills_root = project / ".agents/skills"
    actual_paths = {
        (path / "SKILL.md").relative_to(project).as_posix()
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    } if skills_root.is_dir() else set()
    declared = set(declared_paths)
    for path in sorted(actual_paths - declared):
        errors.append(f"skills: unregistered SKILL.md: {path}")
    for path in sorted(declared - actual_paths):
        errors.append(f"skills: manifest path has no SKILL.md: {path}")


def _validate_contracts(project: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    contracts = _mapping(manifest.get("contracts"), "contracts", errors)
    schemas = _mapping(contracts.get("schemas"), "contracts.schemas", errors)
    declared_paths: list[str] = []
    for title, raw_entry in sorted(schemas.items()):
        if not isinstance(title, str) or not title:
            errors.append("contracts.schemas: contract names must be non-empty strings")
            continue
        entry = _mapping(raw_entry, f"contracts.schemas.{title}", errors)
        raw_path = entry.get("path")
        version = entry.get("version")
        if not isinstance(version, str) or not version:
            errors.append(f"contracts.schemas.{title}.version: expected a non-empty string")
        if isinstance(raw_path, str):
            declared_paths.append(raw_path.replace("\\", "/"))
        path = _safe_path(project, raw_path, f"contracts.schemas.{title}.path", errors)
        if path is None:
            continue
        if not path.is_file():
            errors.append(f"contracts.schemas.{title}.path: missing file: {raw_path}")
            continue
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{path.relative_to(project)}: cannot load JSON Schema: {exc}")
            continue
        if not isinstance(schema, dict):
            errors.append(f"{path.relative_to(project)}: JSON Schema root must be an object")
            continue
        if schema.get("$schema") != DRAFT_2020_12:
            errors.append(f"{path.relative_to(project)}: $schema must be {DRAFT_2020_12}")
        try:
            validator_for(schema).check_schema(schema)
        except jsonschema_exceptions.SchemaError as exc:
            errors.append(f"{path.relative_to(project)}: invalid JSON Schema: {exc.message}")
        if schema.get("title") != title:
            errors.append(
                f"{path.relative_to(project)}: schema title {schema.get('title')!r} "
                f"does not match manifest key {title!r}"
            )
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        version_property = properties.get("schema_version")
        if not isinstance(version_property, dict):
            version_property = {}
        schema_version = version_property.get("const")
        if schema_version != version:
            errors.append(
                f"{path.relative_to(project)}: schema_version const {schema_version!r} "
                f"does not match contracts.schemas.{title}.version {version!r}"
            )
        schema_id = schema.get("$id")
        expected_suffix = "/" + path.relative_to(project).as_posix()
        if not isinstance(schema_id, str) or not schema_id.endswith(expected_suffix):
            errors.append(f"{path.relative_to(project)}: $id must end with {expected_suffix}")
    for duplicate in _duplicate_values(declared_paths):
        errors.append(f"contracts.schemas: duplicate path: {duplicate}")

    contracts_root = project / "contracts"
    actual_paths = {
        path.relative_to(project).as_posix()
        for path in contracts_root.glob("*.schema.json")
        if path.is_file()
    }
    declared = set(declared_paths)
    for path in sorted(actual_paths - declared):
        errors.append(f"contracts.schemas: unregistered JSON Schema: {path}")
    for path in sorted(declared - actual_paths):
        errors.append(f"contracts.schemas: manifest path has no JSON Schema: {path}")


def _validate_config_and_docs(project: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    config_path = project / ".codex/config.toml"
    if not config_path.is_file():
        errors.append(".codex/config.toml: missing file")
    else:
        config = _load_toml(config_path, project, errors)
        config_agents = _mapping(config.get("agents"), ".codex/config.toml agents", errors)
        orchestration = _mapping(manifest.get("orchestration"), "orchestration", errors)
        actual = config_agents.get("max_concurrent_threads_per_session")
        expected = orchestration.get("max_concurrent_agents")
        if actual != expected:
            errors.append(
                ".codex/config.toml: max_concurrent_threads_per_session "
                f"{actual!r} does not match manifest max_concurrent_agents {expected!r}"
            )

    documentation = _mapping(manifest.get("documentation"), "documentation", errors)
    for name, raw_path in sorted(documentation.items()):
        path = _safe_path(project, raw_path, f"documentation.{name}", errors)
        if path is not None and not path.exists():
            errors.append(f"documentation.{name}: missing path: {raw_path}")


def validate_project(project: Path) -> list[str]:
    """Return deterministic synchronization errors for ``project``."""

    project = project.resolve()
    manifest_path = project / "harness-manifest.yaml"
    if not manifest_path.is_file():
        return ["harness-manifest.yaml: missing file"]
    errors: list[str] = []
    manifest = _load_yaml(manifest_path, errors)
    _validate_agents(project, manifest, errors)
    _validate_skills(project, manifest, errors)
    _validate_contracts(project, manifest, errors)
    _validate_config_and_docs(project, manifest, errors)
    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    errors = validate_project(args.project)
    if errors:
        for error in errors:
            print(f"harness-sync: error: {error}", file=sys.stderr)
        return 1
    print("harness-sync: manifest, agents, skills, schemas, config, and docs are synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
