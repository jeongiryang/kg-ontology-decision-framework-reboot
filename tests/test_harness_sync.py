from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts.validation.render_mermaid import extract_blocks, render
from scripts.validation.validate_harness_sync import validate_project


class HarnessSyncTests(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        (root / ".codex/agents").mkdir(parents=True)
        (root / ".agents/skills/example").mkdir(parents=True)
        (root / "contracts").mkdir()
        (root / "docs/harness/decisions").mkdir(parents=True)
        (root / ".codex/config.toml").write_text(
            "[agents]\nmax_concurrent_threads_per_session = 1\n", encoding="utf-8"
        )
        (root / ".codex/agents/example.toml").write_text(
            'name = "example_agent"\ndescription = "example"\n', encoding="utf-8"
        )
        (root / ".agents/skills/example/SKILL.md").write_text(
            "---\nname: example\ndescription: Example skill.\n---\n\n# Example\n",
            encoding="utf-8",
        )
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.invalid/contracts/example.schema.json",
            "title": "Example",
            "type": "object",
            "properties": {"schema_version": {"const": "1.0.0"}},
        }
        (root / "contracts/example.schema.json").write_text(
            json.dumps(schema), encoding="utf-8"
        )
        (root / "docs/harness/README.md").write_text("# Harness\n", encoding="utf-8")
        manifest = {
            "orchestration": {"max_concurrent_agents": 1},
            "agents": [
                {
                    "name": "example_agent",
                    "config": ".codex/agents/example.toml",
                }
            ],
            "skills": [
                {"name": "example", "path": ".agents/skills/example/SKILL.md"}
            ],
            "contracts": {
                "version": "1.0.0",
                "schemas": {"Example": "contracts/example.schema.json"},
            },
            "documentation": {"index": "docs/harness/README.md"},
        }
        (root / "harness-manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )

    def test_valid_project_is_synchronized(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            self.assertEqual(validate_project(project), [])

    def test_agent_name_drift_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            (project / ".codex/agents/example.toml").write_text(
                'name = "drifted_agent"\n', encoding="utf-8"
            )
            errors = validate_project(project)
            self.assertTrue(any("does not match manifest name" in error for error in errors))

    def test_unregistered_skill_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            extra = project / ".agents/skills/extra"
            extra.mkdir()
            (extra / "SKILL.md").write_text(
                "---\nname: extra\ndescription: Extra skill.\n---\n", encoding="utf-8"
            )
            errors = validate_project(project)
            self.assertTrue(any("unregistered SKILL.md" in error for error in errors))

    def test_invalid_json_schema_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            path = project / "contracts/example.schema.json"
            schema = json.loads(path.read_text(encoding="utf-8"))
            schema["type"] = "not-a-json-schema-type"
            path.write_text(json.dumps(schema), encoding="utf-8")
            errors = validate_project(project)
            self.assertTrue(any("invalid JSON Schema" in error for error in errors))

    def test_schema_version_drift_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            path = project / "contracts/example.schema.json"
            schema = json.loads(path.read_text(encoding="utf-8"))
            schema["properties"]["schema_version"]["const"] = "2.0.0"
            path.write_text(json.dumps(schema), encoding="utf-8")
            errors = validate_project(project)
            self.assertTrue(any("does not match contracts.version" in error for error in errors))

    def test_unregistered_schema_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            source = project / "contracts/example.schema.json"
            (project / "contracts/extra.schema.json").write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
            errors = validate_project(project)
            self.assertTrue(any("unregistered JSON Schema" in error for error in errors))

    def test_malformed_config_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            (project / ".codex/config.toml").write_text('agents = "invalid"\n', encoding="utf-8")
            errors = validate_project(project)
            self.assertTrue(any("expected a mapping" in error for error in errors))

    def test_concurrency_drift_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            (project / ".codex/config.toml").write_text(
                "[agents]\nmax_concurrent_threads_per_session = 2\n", encoding="utf-8"
            )
            errors = validate_project(project)
            self.assertTrue(any("max_concurrent_threads_per_session" in error for error in errors))

    def test_mermaid_blocks_are_extracted(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            docs = project / "docs/harness"
            docs.mkdir(parents=True)
            (docs / "architecture.md").write_text(
                "# Architecture\n\n```mermaid\nflowchart LR\nA --> B\n```\n",
                encoding="utf-8",
            )
            blocks = extract_blocks(project)
            self.assertEqual(len(blocks), 1)
            self.assertIn("A --> B", blocks[0][2])

    def test_mermaid_renderer_passes_puppeteer_config(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            docs = project / "docs/harness"
            docs.mkdir(parents=True)
            (docs / "architecture.md").write_text(
                "```mermaid\nflowchart LR\nA --> B\n```\n", encoding="utf-8"
            )
            config = project / "puppeteer.json"
            config.write_text('{"args":["--no-sandbox"]}\n', encoding="utf-8")

            def fake_run(command, **_kwargs):
                output = Path(command[command.index("-o") + 1])
                output.write_text("<svg/>\n", encoding="utf-8")
                self.assertEqual(command[command.index("-p") + 1], str(config))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("scripts.validation.render_mermaid.subprocess.run", side_effect=fake_run):
                self.assertEqual(render(project, project / "rendered", "mmdc", config), 0)


if __name__ == "__main__":
    unittest.main()
