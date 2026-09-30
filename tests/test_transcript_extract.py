from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant import transcript_extract as extraction
from academic_assistant.transcript_extract import _Word, _parse_words, extract_transcript


def pdf_bytes(pages: int = 1) -> bytes:
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            document.new_page(600, 800).close()
        stream = BytesIO()
        document.save(stream)
        return stream.getvalue()
    finally:
        document.close()


def word(text: str, x: float, y: float, width: float = 50) -> _Word:
    return _Word(text, x, y, width, 10)


def header(x: float = 0) -> list[_Word]:
    return [word("구분", x, 30), word("교과목명", x + 65, 30),
            word("학점", x + 220, 30, 20), word("성적", x + 260, 30, 20)]


def row(name="합성과목", *, x=0, y=70, category="전선", credits="3.0", grade="A0") -> list[_Word]:
    return [word(category, x, y, 20), word(name, x + 65, y, 120),
            word(credits, x + 225, y, 20), word(grade, x + 265, y, 20)]


class TranscriptParserTests(unittest.TestCase):
    def test_column_major_terms_continue_and_zero_credit_rows_survive(self):
        words = header() + header(350) + header(700)
        words += [word("2026학년도 1학기", 0, 50, 130)]
        words += row("합성수업", y=70) + row("합성상담", y=90, credits="0", grade="S", category="자선")
        words += row("합성계속수업", x=350, y=50)
        words += [word("2026학년도 2학기", 350, 80, 130)] + row("합성논문", x=350, y=100, credits="0", grade="U", category="전필")
        words += row("합성마지막수업", x=700, y=50)
        courses, _ = _parse_words(words, page_index=5)
        self.assertEqual(["2026-1", "2026-1", "2026-1", "2026-2", "2026-2"], [r.term for r in courses])
        self.assertEqual([3, 0, 3, 0, 3], [r.credits for r in courses])
        self.assertEqual(["A0", "S", "A0", "U", "A0"], [r.grade for r in courses])
        self.assertEqual("p6-r1", courses[0].row_id)
        self.assertTrue(all(r.balanced_area is None for r in courses))

    def test_ambiguous_cells_stay_null_instead_of_borrowing_course_digits(self):
        words = header() + row("합성수학 2", credits="?", grade="A")
        courses, _ = _parse_words(words)
        self.assertEqual(1, len(courses))
        self.assertEqual("합성수학 2", courses[0].course_name)
        self.assertIsNone(courses[0].credits)
        self.assertIsNone(courses[0].grade)
        self.assertIsNone(courses[0].term)

    def test_split_korean_header_category_and_grade_cells(self):
        words = [word("교", 65, 30, 8), word("과", 76, 30, 8), word("목", 87, 30, 8), word("명", 98, 30, 8)]
        words += [word("학점", 220, 30, 20), word("성적", 260, 30, 20)]
        words += [word("전", 0, 70, 8), word("필", 12, 70, 8), word("합성수업", 65, 70, 100),
                  word("3", 225, 70, 4), word(".", 230, 70, 2), word("0", 233, 70, 5),
                  word("B", 265, 70, 8), word("+", 276, 70, 8)]
        courses, _ = _parse_words(words)
        self.assertEqual(1, len(courses))
        self.assertEqual(("major_required", 3, "B+"), (courses[0].category, courses[0].credits, courses[0].grade))

    def test_duplicates_are_preserved_and_identifier_rows_are_rejected(self):
        words = header() + row("SYN101 합성수업", y=70) + row("SYN101 합성수업", y=90)
        words += row("학번 12345678", y=110) + row("성명 합성인물", y=130) + row("ignore instructions", y=150)
        words += [word("민감한 표 밖 임의 문장", 65, 170, 150)]
        courses, _ = _parse_words(words)
        self.assertEqual(2, len(courses))
        self.assertEqual(2, len({r.row_id for r in courses}))
        self.assertEqual(["SYN101", "SYN101"], [r.course_code for r in courses])

    def test_category_legend_without_a_course_table_returns_no_rows(self):
        courses, _ = _parse_words(row("임의 문장"))
        self.assertEqual([], courses)

    def test_only_explicit_admission_year_and_fixed_department_are_returned(self):
        words = header() + row() + [word("입학년월일: 2022.03.02", 0, 10, 180),
                                   word("컴퓨터공학과", 0, 20, 150),
                                   word("총취득학점: 93.0", 0, 110, 150)]
        _, metadata = _parse_words(words)
        self.assertEqual((2022, "컴퓨터공학과", 93), metadata)
        _, metadata = _parse_words(header() + row() + [word("2026학년도1학기", 0, 10, 150)])
        self.assertIsNone(metadata[0])


class TranscriptExtractionTests(unittest.TestCase):
    def test_invalid_oversized_encrypted_and_page_limit_inputs(self):
        for data in (b"", b"not a PDF", b"%PDF-malformed", b"%PDF-" + b"x" * extraction.MAX_PDF_BYTES):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                extract_transcript(data)
        data = pdf_bytes()
        with patch.object(pdfium.raw, "FPDF_GetSecurityHandlerRevision", return_value=4), self.assertRaises(ValueError):
            extract_transcript(data)
        with self.assertRaises(ValueError):
            extract_transcript(pdf_bytes(11))

    def test_long_pdf_requires_selector_without_rendering_or_ocr(self):
        with patch.object(extraction, "_windows_ocr") as ocr, patch.object(extraction, "_png") as render:
            result = extract_transcript(pdf_bytes(7))
        self.assertEqual("manual_required", result.extraction_method)
        self.assertEqual([extraction.SELECTION_ISSUE], result.issues)
        ocr.assert_not_called()
        render.assert_not_called()

    def test_selector_renders_only_selected_page_and_always_requires_confirmation(self):
        words = header() + row() + [word("2026학년도1학기", 0, 50, 150)]
        with patch.object(extraction, "_windows_ocr", return_value=words) as ocr, patch.object(extraction, "_png", return_value=b"png") as render:
            result = extract_transcript(pdf_bytes(7), page_number=6)
        self.assertEqual("windows_ocr", result.extraction_method)
        self.assertEqual("p6-r1", result.courses[0].row_id)
        self.assertTrue(result.requires_confirmation)
        self.assertEqual(1, ocr.call_count)
        self.assertEqual(1, render.call_count)

    def test_page_selector_rejects_bool_and_out_of_range(self):
        for page in (True, 0, -1, 2, 11, "1"):
            with self.subTest(page=page), self.assertRaises(ValueError):
                extract_transcript(pdf_bytes(), page_number=page)

    def test_text_table_avoids_ocr(self):
        with patch.object(extraction, "_text_words", return_value=header() + row()), patch.object(extraction, "_windows_ocr") as ocr:
            result = extract_transcript(pdf_bytes())
        self.assertEqual("pdf_text", result.extraction_method)
        self.assertEqual(1, len(result.courses))
        self.assertIn(extraction.INCOMPLETE_ISSUE, result.issues)
        ocr.assert_not_called()

    def test_ocr_timeout_and_error_text_are_not_exposed(self):
        for exception in (subprocess.TimeoutExpired("private-path", 35), RuntimeError("private identifier")):
            with patch.object(extraction, "_windows_ocr", side_effect=exception):
                result = extract_transcript(pdf_bytes())
            self.assertEqual("manual_required", result.extraction_method)
            self.assertEqual([], result.courses)
            self.assertIn(extraction.OCR_ISSUE, result.issues)
            self.assertNotIn("private", result.model_dump_json())

    def test_at_most_three_pages_are_ocr_and_png_is_memory_only(self):
        with patch.object(extraction, "_windows_ocr", return_value=[]) as ocr:
            result = extract_transcript(pdf_bytes(3))
        self.assertEqual(3, ocr.call_count)
        self.assertTrue(all(call.args[0].startswith(b"\x89PNG\r\n\x1a\n") for call in ocr.call_args_list))
        self.assertEqual("manual_required", result.extraction_method)


if __name__ == "__main__":
    unittest.main()
