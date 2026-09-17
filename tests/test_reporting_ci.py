from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.reporting.ci_checks import (
    check_documentation_freshness,
    check_markdown,
    check_public_reports,
)
from scripts.reporting.reporting import generate_public_report
from tests.test_completion_reporting import valid_report


class ReportingCITests(unittest.TestCase):
    def test_generated_report_bundle_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            generate_public_report(valid_report(), project / "reports")
            self.assertEqual(check_public_reports(project), [])

    def test_generated_pdf_bundle_passes_byte_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            generate_public_report(valid_report(pdf_required=True), project / "reports")
            self.assertEqual(check_public_reports(project), [])

    def test_missing_markdown_pair_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            report = valid_report()
            run_dir = project / "reports/runs"
            run_dir.mkdir(parents=True)
            (run_dir / f"{report['run_id']}.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )
            errors = check_public_reports(project)
            self.assertTrue(any("missing Markdown pair" in item for item in errors))

    def test_orphan_markdown_and_pdf_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            run_dir = project / "reports/runs"
            pdf_dir = project / "reports/pdf"
            run_dir.mkdir(parents=True)
            pdf_dir.mkdir(parents=True)
            (run_dir / "orphan.md").write_text("orphan\n", encoding="utf-8")
            (pdf_dir / "orphan.pdf").write_bytes(b"%PDF-orphan")
            errors = check_public_reports(project)
            self.assertTrue(any("orphan Markdown" in item for item in errors))
            self.assertTrue(any("orphan PDF" in item for item in errors))

    def test_missing_local_markdown_link_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            docs = project / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("[missing](missing.md)\n", encoding="utf-8")
            errors = check_markdown(project)
            self.assertTrue(any("missing local link" in item for item in errors))

    def test_tampered_markdown_fails_byte_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            report = valid_report()
            generate_public_report(report, project / "reports")
            markdown_path = project / "reports/runs" / f"{report['run_id']}.md"
            markdown_path.write_text("tampered\n", encoding="utf-8")
            errors = check_public_reports(project)
            self.assertTrue(any("does not byte-match" in item for item in errors))

    def test_tampered_pdf_fails_byte_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            report = valid_report(pdf_required=True)
            generate_public_report(report, project / "reports")
            pdf_path = project / "reports/pdf" / f"{report['run_id']}.pdf"
            pdf_path.write_bytes(pdf_path.read_bytes() + b"tampered")
            errors = check_public_reports(project)
            self.assertTrue(any("does not byte-match" in item for item in errors))

    def test_expanded_harness_changes_require_docs_and_adr(self):
        changed_paths = (
            ".codex/config.toml",
            ".github/workflows/harness-ci.yml",
            "scripts/reporting/reporting.py",
            "scripts/validation/validate_harness_sync.py",
            "harness-manifest.yaml",
        )
        for changed_path in changed_paths:
            with self.subTest(changed_path=changed_path):
                completed = SimpleNamespace(
                    returncode=0,
                    stdout=f"{changed_path}\n",
                    stderr="",
                )
                with patch("scripts.reporting.ci_checks.subprocess.run", return_value=completed):
                    errors = check_documentation_freshness(Path.cwd(), "base")
                self.assertTrue(any("docs/harness Markdown" in item for item in errors))
                self.assertTrue(any("ADR" in item for item in errors))

    def test_expanded_harness_change_with_docs_and_adr_passes(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=(
                "scripts/reporting/reporting.py\n"
                "docs/harness/reporting.md\n"
                "docs/harness/decisions/0005-contract-sync-ci.md\n"
            ),
            stderr="",
        )
        with patch("scripts.reporting.ci_checks.subprocess.run", return_value=completed):
            self.assertEqual(check_documentation_freshness(Path.cwd(), "base"), [])


if __name__ == "__main__":
    unittest.main()
