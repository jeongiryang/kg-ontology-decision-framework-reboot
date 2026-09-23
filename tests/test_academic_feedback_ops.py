from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.cli import main
from academic_assistant.feedback import FeedbackSummaryError, store_feedback, summarize_feedback
from academic_assistant.models import AcademicFeedbackRequest


def record(
    suffix: str,
    *,
    packet: str = "a" * 32,
    status: str = "insufficient_evidence",
    question: str = "캡스톤 운영 기준을 확인해 주세요",
    category: str = "missing_evidence",
) -> dict:
    return {
        "schema_version": "1.0.0",
        "feedback_id": f"feedback-{suffix * 24}",
        "received_at": "2026-09-23T01:02:03+00:00",
        "scope": {
            "admission_year": 2026,
            "matched_curriculum_year": 2026,
            "department": "컴퓨터공학과",
        },
        "packet_id": f"academic-{packet}",
        "status": status,
        "question": question,
        "category": category,
    }


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8")


class AcademicFeedbackOpsTests(unittest.TestCase):
    def test_missing_file_is_successful_zero_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.jsonl"
            summary = summarize_feedback(path)
            self.assertEqual(0, summary.record_count)
            self.assertEqual(0, summary.duplicate_count)
            self.assertEqual({"insufficient_evidence": 0, "conflict": 0}, summary.status_counts)
            self.assertEqual(
                {"missing_evidence": 0, "unclear_question": 0, "scope_request": 0, "other": 0},
                summary.category_counts,
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["feedback-summary", "--path", str(path)])
            self.assertEqual(0, exit_code)
            self.assertIn("records=0", stdout.getvalue())

    def test_default_path_uses_environment_and_missing_is_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-created.jsonl"
            stdout = io.StringIO()
            with patch.dict(os.environ, {"ACADEMIC_FEEDBACK_PATH": str(path)}), redirect_stdout(stdout):
                exit_code = main(["feedback-summary", "--json"])
            self.assertEqual(0, exit_code)
            self.assertEqual(0, json.loads(stdout.getvalue())["record_count"])

    def test_valid_file_summarizes_status_category_and_duplicates(self) -> None:
        secret_question = "캡스톤 세부 순서를 확인해 주세요"
        values = [
            record("1", question=secret_question),
            record("2", question=secret_question),
            record("3", packet="b" * 32, status="conflict", question="논문 규칙 충돌 확인", category="other"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            write_jsonl(path, values)
            summary = summarize_feedback(path)
            self.assertEqual(3, summary.record_count)
            self.assertEqual({"insufficient_evidence": 2, "conflict": 1}, summary.status_counts)
            self.assertEqual(1, summary.duplicate_count)
            self.assertEqual(2, summary.category_counts["missing_evidence"])
            self.assertEqual(1, summary.category_counts["other"])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["feedback-summary", "--path", str(path)])
            output = stdout.getvalue()
            self.assertEqual(0, exit_code)
            self.assertIn("duplicates=1", output)
            self.assertNotIn(secret_question, output)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(0, main(["feedback-summary", "--path", str(path), "--json"]))
            payload = json.loads(stdout.getvalue())
            self.assertEqual(3, payload["record_count"])
            self.assertNotIn("question", payload)

    def test_store_and_summary_integration_does_not_disclose_question(self) -> None:
        question = "PCCP 운영 근거를 보완해 주세요"
        request = AcademicFeedbackRequest(
            packet_id="academic-" + "c" * 32,
            status="insufficient_evidence",
            question=question,
            category="scope_request",
            consent_to_store=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(path),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(path.parent),
            }):
                store_feedback(request)
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    self.assertEqual(0, main(["feedback-summary", "--json"]))
            payload = json.loads(stdout.getvalue())
            self.assertEqual(1, payload["record_count"])
            self.assertEqual(1, payload["category_counts"]["scope_request"])
            self.assertNotIn(question, stdout.getvalue())

    def test_common_korean_name_with_particle_is_rejected_on_store_and_summary(self) -> None:
        identifying_question = "홍길동의 PCCP 기준은 무엇인가요?"
        request = AcademicFeedbackRequest(
            packet_id="academic-" + "d" * 32,
            status="insufficient_evidence",
            question=identifying_question,
            category="missing_evidence",
            consent_to_store=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(path),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(path.parent),
            }):
                with self.assertRaises(ValueError):
                    store_feedback(request)
            self.assertFalse(path.exists())

            write_jsonl(path, [record("1", question=identifying_question)])
            with self.assertRaisesRegex(FeedbackSummaryError, "^invalid feedback file$"):
                summarize_feedback(path)

    def test_nfkc_normalized_fullwidth_student_number_is_rejected(self) -> None:
        identifying_question = "２０２６１２３４５６ PCCP 기준"
        request = AcademicFeedbackRequest(
            packet_id="academic-" + "f" * 32,
            status="insufficient_evidence",
            question=identifying_question,
            category="missing_evidence",
            consent_to_store=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(path),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(path.parent),
            }):
                with self.assertRaises(ValueError):
                    store_feedback(request)
            self.assertFalse(path.exists())

            write_jsonl(path, [record("1", question=identifying_question)])
            with self.assertRaisesRegex(FeedbackSummaryError, "^invalid feedback file$"):
                summarize_feedback(path)

    def test_normal_academic_terms_are_not_mistaken_for_names(self) -> None:
        questions = (
            "졸업학점과 이수학점 기준을 확인해 주세요",
            "캡스톤은 어떤 근거가 필요한가요?",
            "전과생은 어느 교육과정을 적용하나요?",
            "한학점은 어떻게 계산하나요?",
            "공모전의 졸업요건",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(path),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(path.parent),
            }):
                for index, question in enumerate(questions, start=1):
                    stored = store_feedback(AcademicFeedbackRequest(
                        packet_id="academic-" + str(index) * 32,
                        status="insufficient_evidence",
                        question=question,
                        category="missing_evidence",
                        consent_to_store=True,
                    ))
                    self.assertTrue(stored.stored)
            summary = summarize_feedback(path)
            self.assertEqual(5, summary.record_count)
            self.assertEqual(0, summary.duplicate_count)

    def test_store_rejects_destination_outside_approved_private_root(self) -> None:
        request = AcademicFeedbackRequest(
            packet_id="academic-" + "e" * 32,
            status="insufficient_evidence",
            question="캡스톤 운영 근거를 확인해 주세요",
            category="missing_evidence",
            consent_to_store=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "feedback.jsonl"
            private_root = root / "private"
            with patch.dict(os.environ, {
                "ACADEMIC_FEEDBACK_PATH": str(destination),
                "ACADEMIC_FEEDBACK_PRIVATE_ROOT": str(private_root),
            }):
                with self.assertRaises(OSError):
                    store_feedback(request)
            self.assertFalse(destination.exists())

    def test_corrupt_pii_and_contract_violations_fail_closed(self) -> None:
        mutations = {
            "extra-field": lambda value: value.__setitem__("extra", True),
            "wrong-version": lambda value: value.__setitem__("schema_version", "2.0.0"),
            "bad-feedback-id": lambda value: value.__setitem__("feedback_id", "feedback-bad"),
            "bad-packet-id": lambda value: value.__setitem__("packet_id", "academic-bad"),
            "bad-time": lambda value: value.__setitem__("received_at", "2026-09-23"),
            "bad-scope": lambda value: value["scope"].__setitem__("department", "기계공학과"),
            "bad-status": lambda value: value.__setitem__("status", "supported"),
            "bad-category": lambda value: value.__setitem__("category", "new_category"),
            "pii": lambda value: value.__setitem__("question", "학번 2026123456의 캡스톤 기준"),
            "untrimmed": lambda value: value.__setitem__("question", " 캡스톤 기준 "),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "feedback.jsonl"
                value = record("1")
                mutate(value)
                write_jsonl(path, [value])
                with self.assertRaisesRegex(FeedbackSummaryError, "^invalid feedback file$"):
                    summarize_feedback(path)

    def test_malformed_input_emits_no_partial_summary_or_record_content(self) -> None:
        secret = "캡스톤 내부 확인 문장"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.jsonl"
            path.write_text(json.dumps(record("1", question=secret), ensure_ascii=False) + "\n{broken\n", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["feedback-summary", "--path", str(path)])
            self.assertEqual(65, exit_code)
            self.assertEqual("", stdout.getvalue())
            self.assertEqual("invalid feedback file", stderr.getvalue().strip())
            self.assertNotIn(secret, stderr.getvalue())

    def test_invalid_utf8_blank_line_and_directory_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_utf8 = root / "invalid.jsonl"
            invalid_utf8.write_bytes(b"\xff")
            blank = root / "blank.jsonl"
            blank.write_text("\n", encoding="utf-8")
            for path in (invalid_utf8, blank, root):
                with self.subTest(path=path), self.assertRaises(FeedbackSummaryError):
                    summarize_feedback(path)

    def test_duplicate_json_keys_nonstandard_constants_and_noncanonical_time_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate_key = root / "duplicate-key.jsonl"
            encoded = json.dumps(record("1"), ensure_ascii=False)
            duplicate_key.write_text(encoded[:-1] + ',"status":"conflict"}\n', encoding="utf-8")
            nonstandard = root / "nonstandard.jsonl"
            nonstandard.write_text(encoded[:-1] + ',"extra":NaN}\n', encoding="utf-8")
            noncanonical_time = root / "time.jsonl"
            value = record("1")
            value["received_at"] = "2026-09-23T01:02:03.000+00:00"
            write_jsonl(noncanonical_time, [value])
            for path in (duplicate_key, nonstandard, noncanonical_time):
                with self.subTest(path=path), self.assertRaises(FeedbackSummaryError):
                    summarize_feedback(path)


if __name__ == "__main__":
    unittest.main()
