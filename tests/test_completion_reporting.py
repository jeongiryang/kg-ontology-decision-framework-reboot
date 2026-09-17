from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.reporting.reporting as reporting_module
from scripts.reporting.reporting import (
    ReportValidationError,
    SensitiveDataError,
    generate_public_report,
    redact_absolute_paths,
    render_markdown,
    render_pdf,
    sanitize_report,
    validate_completion_report,
)


def valid_report(*, pdf_required: bool = False) -> dict:
    return {
        "schema_version": "1.0.0",
        "run_id": "20260917-report-test",
        "title": "완료 보고서 테스트",
        "request": "하네스 완료 보고 체계를 구축한다.",
        "summary": "결정적 공개 보고서를 생성했다.",
        "harness_version": "codex-harness@79b82281",
        "git": {
            "branch": "codex/reporting",
            "base_commit": "abc1234",
            "head_commit": "def5678",
        },
        "changes": [
            {
                "kind": "added",
                "path": "scripts/reporting/generate_report.py",
                "summary": "보고서 생성기를 추가했다.",
            }
        ],
        "agents": [
            {
                "role": "harness_worker",
                "task_id": "completion-reporting",
                "status": "completed",
                "summary": "생성과 검사를 구현했다.",
            }
        ],
        "checks": [
            {
                "name": "unit tests",
                "required": True,
                "status": "passed",
                "command": "python -m unittest",
                "evidence": "8 tests passed",
            }
        ],
        "academic_sources_changed": [],
        "issues": [],
        "publication": {"major": pdf_required, "pdf_required": pdf_required},
    }


class CompletionReportingTests(unittest.TestCase):
    def test_minor_report_creates_json_and_markdown_only(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "reports"
            result = generate_public_report(valid_report(), output)
            self.assertFalse(result["pdf_generated"])
            self.assertTrue((output / "runs/20260917-report-test.json").is_file())
            markdown = (output / "runs/20260917-report-test.md").read_text(encoding="utf-8")
            self.assertIn("완료 보고서 테스트", markdown)
            self.assertIn(valid_report()["request"], markdown)
            self.assertFalse((output / "pdf/20260917-report-test.pdf").exists())

    def test_markdown_renders_every_required_report_section(self):
        report = valid_report()
        report["academic_sources_changed"] = ["2026 교육과정의 해시만 갱신"]
        markdown = render_markdown(report)
        for expected in (
            report["request"],
            report["harness_version"],
            report["git"]["branch"],
            report["git"]["base_commit"],
            report["git"]["head_commit"],
            report["agents"][0]["role"],
            report["agents"][0]["task_id"],
            report["agents"][0]["status"],
            report["academic_sources_changed"][0],
        ):
            self.assertIn(expected, markdown)

    def test_major_report_pdf_is_deterministic(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            report = valid_report(pdf_required=True)
            first_result = generate_public_report(report, Path(first) / "reports")
            second_result = generate_public_report(report, Path(second) / "reports")
            first_pdf = Path(first_result["artifacts"][-1]).read_bytes()
            second_pdf = Path(second_result["artifacts"][-1]).read_bytes()
            self.assertTrue(first_pdf.startswith(b"%PDF"))
            self.assertEqual(hashlib.sha256(first_pdf).digest(), hashlib.sha256(second_pdf).digest())

    def test_pdf_renderer_receives_every_required_report_section(self):
        report = valid_report(pdf_required=True)
        report["academic_sources_changed"] = ["2026 교육과정 근거 갱신"]
        report["issues"] = [{"severity": "info", "message": "후속 챗봇 구현은 범위 밖"}]
        rendered_values: list[str] = []
        original_paragraph = reporting_module._pdf_paragraph

        def record_paragraph(value, style):
            rendered_values.append(str(value))
            return original_paragraph(value, style)

        with tempfile.TemporaryDirectory() as temp:
            with patch(
                "scripts.reporting.reporting._pdf_paragraph",
                side_effect=record_paragraph,
            ):
                render_pdf(report, Path(temp) / "report.pdf")

        for expected in (
            report["request"],
            report["harness_version"],
            report["git"]["branch"],
            report["git"]["base_commit"],
            report["git"]["head_commit"],
            report["agents"][0]["role"],
            report["agents"][0]["task_id"],
            report["agents"][0]["status"],
            report["academic_sources_changed"][0],
            report["issues"][0]["message"],
        ):
            self.assertTrue(any(expected in value for value in rendered_values), expected)

    def test_absolute_paths_are_redacted_from_narrative_fields(self):
        report = valid_report()
        report["summary"] = r"Windows C:\Users\student\secret and Unix /home/student/project/file.txt"
        report["checks"][0]["command"] = r"python C:\work\project\test.py /home/user/input.json"
        sanitized = sanitize_report(report)
        self.assertNotIn("C:\\", json.dumps(sanitized))
        self.assertNotIn("/home/", json.dumps(sanitized))
        self.assertIn("<redacted-path>", sanitized["summary"])

    def test_relative_change_path_is_preserved(self):
        report = sanitize_report(valid_report())
        self.assertEqual(report["changes"][0]["path"], "scripts/reporting/generate_report.py")

    def test_secret_token_aborts_before_outputs(self):
        report = valid_report()
        report["summary"] = "token sk-abcdefghijklmnopqrstuvwxyz012345"
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "reports"
            with self.assertRaises(SensitiveDataError):
                generate_public_report(report, output)
            self.assertFalse(output.exists())

    def test_student_identifier_field_is_rejected(self):
        report = valid_report()
        report["student_id"] = "202612345"
        with self.assertRaises(SensitiveDataError):
            validate_completion_report(report)

    def test_missing_required_field_is_rejected(self):
        newly_required = (
            "request",
            "harness_version",
            "git",
            "agents",
            "academic_sources_changed",
        )
        for field in newly_required:
            with self.subTest(field=field):
                report = valid_report()
                del report[field]
                with self.assertRaises(ReportValidationError):
                    validate_completion_report(report)

    def test_full_git_baseline_is_required(self):
        for field in ("branch", "base_commit", "head_commit"):
            with self.subTest(field=field):
                report = valid_report()
                del report["git"][field]
                with self.assertRaises(ReportValidationError):
                    validate_completion_report(report)

    def test_agent_result_fields_are_required(self):
        for field in ("role", "task_id", "status", "summary"):
            with self.subTest(field=field):
                report = valid_report()
                del report["agents"][0][field]
                with self.assertRaises(ReportValidationError):
                    validate_completion_report(report)

    def test_repository_json_schema_accepts_valid_report(self):
        schema = Path(__file__).resolve().parents[1] / "contracts/completion-report.schema.json"
        if not schema.is_file():
            self.skipTest("repository CompletionReport schema is not available")
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema dependency is not installed")
        validate_completion_report(valid_report(), schema_path=schema)

    def test_passed_check_requires_evidence(self):
        report = valid_report()
        report["checks"][0]["evidence"] = ""
        with self.assertRaises(ReportValidationError):
            validate_completion_report(report)

    def test_not_run_reason_is_preserved(self):
        report = valid_report()
        report["checks"][0].update(
            {"status": "not_run", "command": None, "evidence": "브라우저 환경이 없음"}
        )
        validate_completion_report(report)
        with tempfile.TemporaryDirectory() as temp:
            generate_public_report(report, Path(temp) / "reports")

    def test_change_path_cannot_escape_repository(self):
        report = valid_report()
        report["changes"][0]["path"] = "../outside.txt"
        with self.assertRaises(ReportValidationError):
            validate_completion_report(report)

    def test_input_is_not_mutated(self):
        report = valid_report()
        original = copy.deepcopy(report)
        with tempfile.TemporaryDirectory() as temp:
            generate_public_report(report, Path(temp) / "reports")
        self.assertEqual(report, original)

    def test_path_redactor_does_not_change_web_urls(self):
        value = "See https://github.com/example/repo and reports/runs/example.md"
        self.assertEqual(redact_absolute_paths(value), value)

    def test_server_connection_fields_are_rejected(self):
        for field in ("ssh_user", "ssh_host", "server_address", "ip_address"):
            with self.subTest(field=field):
                report = valid_report()
                report[field] = "internal.example.edu"
                with self.assertRaises(SensitiveDataError):
                    validate_completion_report(report)

    def test_server_connection_values_are_rejected(self):
        values = (
            "ssh hgKim@dsw.example.edu",
            "ssh dsw.example.edu",
            "upload to hgKim@dsw.example.edu:/srv/project",
            "server address: dsw.example.edu",
            "GPU host IP is 10.20.30.40",
            "GPU host IPv6 is 2001:db8:85a3::8a2e:370:7334",
            "loopback ::1",
        )
        for value in values:
            with self.subTest(value=value):
                report = valid_report()
                report["summary"] = value
                with self.assertRaises(SensitiveDataError):
                    validate_completion_report(report)


if __name__ == "__main__":
    unittest.main()
