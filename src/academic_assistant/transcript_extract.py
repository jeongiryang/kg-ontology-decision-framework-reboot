"""Bounded local extraction of identifier-free transcript candidates.

Uploaded bytes, rendered pixels and OCR words never leave memory. Only rows
anchored to a recognizable course table are returned; all require confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import zlib

import pypdfium2 as pdfium

from .transcript_models import ExtractedCourse, TranscriptExtraction

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_OCR_PAGES = 3
MAX_PIXELS = 12_000_000
MAX_DIMENSION = 4096
OCR_TIMEOUT_SECONDS = 35
MAX_WORDS = 20_000
CONFIRMATION_ISSUE = "추출한 과목·학점·성적·학기를 원본과 대조하고 직접 확인해 주세요."
SELECTION_ISSUE = "3페이지를 넘는 PDF는 성적표 페이지를 직접 선택해 주세요."
OCR_ISSUE = "로컬 한국어 OCR을 사용할 수 없거나 제한 시간 내 완료하지 못했습니다. 수동 입력이 필요합니다."
NO_ROWS_ISSUE = "확인 가능한 성적표 과목 행을 찾지 못했습니다. 수동 입력이 필요합니다."
INCOMPLETE_ISSUE = "인식하지 못한 학점·성적·학기가 있습니다. 원본과 대조해 직접 입력해 주세요."
TRUNCATED_ISSUE = "과목 수 제한을 초과했습니다. 전체 기록을 수동으로 확인해 주세요."

_CATEGORIES = {
    "기초교양": "foundation", "기교": "foundation",
    "균형교양": "balanced", "균교": "balanced",
    "확대교양": "expanded", "확교": "expanded",
    "전공필수": "major_required", "전필": "major_required",
    "전공선택": "major_elective", "전선": "major_elective",
    "자유선택": "free", "자선": "free",
}
_GRADES = {"A+", "A0", "B+", "B0", "C+", "C0", "D+", "D0", "F", "F0", "P", "PASS", "S", "U", "W"}
_HEADER = re.compile(r"교과목명|과목명|교과목|강좌명")
_PRIVATE_LABEL = re.compile(r"성명|이름|학번|주민|생년|전화|이메일|주소|입학|등록번호|발급번호|증명서|취득학점|평균평점|총학점|합계|총계|이수구분|교과목명|과목명|성적등급|학과|성적증명|백분위|석차|주의|명령|지시|ignore|instruction", re.I)
_TERM = re.compile(r"(20\d{2})\s*(?:학년도|학년|년도|년)?\s*[.\-:/]?\s*(?:제\s*)?([12])\s*학기")


@dataclass(frozen=True)
class _Word:
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _lines(words: list[_Word]) -> list[list[_Word]]:
    """Group by vertical center without joining neighboring table columns."""
    lines: list[list[_Word]] = []
    for word in sorted(words, key=lambda item: (item.center_y, item.x)):
        if not word.text.strip() or word.height <= 0:
            continue
        if lines and abs(word.center_y - sum(w.center_y for w in lines[-1]) / len(lines[-1])) <= max(2, word.height * .45):
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda word: word.x) for line in lines]


def _join_known_cells(words: list[_Word]) -> list[_Word]:
    """Reassemble split printed headings/categories, never repair course names."""
    vocabulary = set(_CATEGORIES) | _GRADES | {"교과목명", "과목명", "교과목", "강좌명", "학점", "성적"}
    joined = []
    for line in _lines(words):
        i = 0
        while i < len(line):
            end = i + 1
            for stop in range(i + 1, min(i + 7, len(line)) + 1):
                group = line[i:stop]
                if any(b.x - (a.x + a.width) > max(a.height, b.height) * 1.4 for a, b in zip(group, group[1:])):
                    break
                value = _compact("".join(word.text for word in group))
                if value in vocabulary or (len(group) > 1 and re.fullmatch(r"\d{1,2}\.0", value)):
                    end = stop
            group = line[i:end]
            first = group[0]
            joined.append(_Word("".join(word.text for word in group), first.x, min(w.y for w in group),
                                max(w.x + w.width for w in group) - first.x, max(w.height for w in group)))
            i = end
    return joined


def _metadata(words: list[_Word]) -> tuple[int | None, str | None, int | None]:
    text = "\n".join(_compact(" ".join(word.text for word in line)) for line in _lines(words))
    years = set(re.findall(r"입학(?:\s*(?:일자|일|년월일|년도))?\s*[:：]?\s*(20\d{2})(?=\s*[.년/\-])", text))
    totals = set(re.findall(r"(?:총\s*)?취득\s*(?:학점|학점계)\s*[:：]?\s*(\d{1,3})(?:\.0)?(?![\d.])", text))
    return (
        int(next(iter(years))) if len(years) == 1 else None,
        "컴퓨터공학과" if "컴퓨터공학과" in _compact(text) else None,
        int(next(iter(totals))) if len(totals) == 1 else None,
    )


def _safe_label(value: str) -> bool:
    return bool(value and len(value) <= 100 and re.search(r"[가-힣A-Za-z]", value)
                and not re.search(r"[@<>]|\d{6,}|https?://|[\\{};]", value)
                and not _PRIVATE_LABEL.search(_compact(value)))


def _parse_words(words: list[_Word], *, page_index: int = 0) -> tuple[list[ExtractedCourse], tuple[int | None, str | None, int | None]]:
    """Read category-anchored rows in column-major semester order."""
    metadata = _metadata(words)
    words = _join_known_cells(words)
    lines = _lines(words)
    headers = [word for word in words if _HEADER.search(_compact(word.text))]
    if not headers:
        return [], metadata
    anchors = [word for word in words if _compact(word.text) in _CATEGORIES]
    # Category positions identify independently repeated table columns.
    columns: list[list[_Word]] = []
    for anchor in sorted(anchors, key=lambda word: word.x):
        if not columns or anchor.x - sum(w.x for w in columns[-1]) / len(columns[-1]) > max(20, anchor.height * 3):
            columns.append([anchor])
        else:
            columns[-1].append(anchor)
    courses: list[ExtractedCourse] = []
    term: str | None = None
    for column_index, column in enumerate(columns):
        left = min(word.x for word in column) - max(3, column[0].height)
        next_left = min(word.x for word in columns[column_index + 1]) if column_index + 1 < len(columns) else math.inf
        # A heading belongs to this column only when above/near its row range.
        column_headers = [header for header in headers if left <= header.x < next_left]
        if not column_headers:
            continue
        table_header = min(column_headers, key=lambda word: word.y)
        header_cells = [w for w in words if table_header.x < w.x < next_left
                        and abs(w.center_y - table_header.center_y) <= table_header.height]
        credit_headers = [w for w in header_cells if _compact(w.text) == "학점"]
        grade_headers = [w for w in header_cells if _compact(w.text) == "성적"]
        credit_x = min((w.x - w.height for w in credit_headers), default=None)
        grade_x = min((w.x - w.height for w in grade_headers), default=None)
        for line in lines:
            segment = [word for word in line if left <= word.x < next_left]
            if not segment:
                continue
            line_text = _compact(" ".join(word.text for word in segment))
            match = _TERM.search(line_text)
            if match:
                term = f"{match.group(1)}-{match.group(2)}"
                continue
            if re.search(r"20\d{2}.*(?:하계|동계|계절)", line_text):
                year = re.search(r"20\d{2}", line_text)
                term = f"{year.group()}-S" if year else None
                continue
            anchor_indices = [i for i, word in enumerate(segment) if _compact(word.text) in _CATEGORIES]
            if len(anchor_indices) != 1:
                continue
            anchor_index = anchor_indices[0]
            # This word must be a real row anchor, not the category legend.
            anchor = segment[anchor_index]
            if abs(anchor.x - sum(w.x for w in column) / len(column)) > max(10, anchor.height * 2):
                continue
            cells = segment[anchor_index + 1:]
            if not cells:
                continue
            grade_index = next((i for i in range(len(cells) - 1, -1, -1)
                                if _compact(cells[i].text).upper() in _GRADES
                                and (grade_x is None or cells[i].x >= grade_x)), None)
            grade = _compact(cells[grade_index].text).upper() if grade_index is not None else None
            numeric = [i for i, word in enumerate(cells) if re.fullmatch(r"\d{1,2}(?:\.0)?", _compact(word.text))
                       and (credit_x is None or word.x >= credit_x)
                       and (grade_x is None or word.x < grade_x)]
            credit_index = next((i for i in reversed(numeric) if grade_index is None or i < grade_index), None)
            credits = int(float(_compact(cells[credit_index].text))) if credit_index is not None else None
            if credits is not None and not 0 <= credits <= 30:
                credits = None
            # The fixed trailing grade cell remains outside the course label
            # even when its value is ambiguous (e.g. a bare A).
            if credit_x is not None:
                name_words = [word for word in cells if word.x < credit_x]
            else:
                name_end = credit_index if credit_index is not None else grade_index
                if name_end is None:
                    continue
                name_words = cells[:name_end]
            code = None
            if name_words and re.fullmatch(r"[A-Z][A-Z0-9]{2,15}", _compact(name_words[0].text)) and re.search(r"\d", name_words[0].text):
                code = _compact(name_words.pop(0).text)
            name = " ".join(word.text.strip() for word in name_words).strip()
            prefix = re.match(r"^([A-Z][A-Z0-9]{2,15})\s+(.+)$", name)
            if code is None and prefix and re.search(r"\d", prefix.group(1)):
                code, name = prefix.group(1), prefix.group(2)
            if not _safe_label(name):
                continue
            courses.append(ExtractedCourse(
                row_id=f"p{page_index + 1}-r{len(courses) + 1}", course_code=code,
                course_name=name, credits=credits, grade=grade,
                category=_CATEGORIES[_compact(anchor.text)], term=term,
            ))
    return courses, metadata


def _text_words(page: pdfium.PdfPage) -> list[_Word]:
    textpage = page.get_textpage()
    try:
        count = textpage.count_chars()
        if count > 150_000:
            raise ValueError("PDF text exceeds extraction limit")
        height = page.get_height()
        words: list[_Word] = []
        chars: list[str] = []
        boxes: list[tuple[float, float, float, float]] = []
        def finish() -> None:
            if chars and boxes:
                left = min(box[0] for box in boxes)
                right = max(box[2] for box in boxes)
                bottom = min(box[1] for box in boxes)
                top = max(box[3] for box in boxes)
                words.append(_Word("".join(chars), left, height - top, right - left, top - bottom))
            chars.clear()
            boxes.clear()
        for index in range(count):
            char = textpage.get_text_range(index, 1, errors="ignore")
            box = textpage.get_charbox(index)
            if not char.strip() or box[3] <= box[1]:
                finish()
                continue
            if boxes and (abs(box[1] - boxes[-1][1]) > max(2, box[3] - box[1]) or box[0] - boxes[-1][2] > max(3, (box[3] - box[1]) * .8)):
                finish()
            chars.append(char)
            boxes.append(box)
        finish()
        return words
    finally:
        textpage.close()


def _png(page: pdfium.PdfPage) -> bytes:
    width, height = page.get_size()
    if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
        raise ValueError("Invalid PDF page dimensions")
    scale = min(3.0, MAX_DIMENSION / max(width, height), math.sqrt(MAX_PIXELS / (width * height)))
    bitmap = page.render(scale=scale, grayscale=True)
    try:
        # A grayscale PNG encoder avoids an additional Pillow dependency.
        if bitmap.mode != "L" or bitmap.width * bitmap.height > MAX_PIXELS or max(bitmap.width, bitmap.height) > MAX_DIMENSION:
            raise ValueError("PDF raster exceeds extraction limit")
        pixels = memoryview(bitmap.buffer).cast("B")
        compressor = zlib.compressobj()
        parts = []
        for y in range(bitmap.height):
            parts.append(compressor.compress(b"\0" + pixels[y * bitmap.stride:y * bitmap.stride + bitmap.width].tobytes()))
        parts.append(compressor.flush())
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", bitmap.width, bitmap.height, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", b"".join(parts)) + chunk(b"IEND", b""))
    finally:
        bitmap.close()


def _windows_ocr(png: bytes) -> list[_Word]:
    if os.name != "nt":
        raise RuntimeError("Local OCR unavailable")
    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise RuntimeError("Local OCR unavailable")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(Path(__file__).with_name("ocr_windows.ps1"))],
        input=png, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=OCR_TIMEOUT_SECONDS, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode or len(result.stdout) > 2_000_000:
        raise RuntimeError("Local OCR unavailable")
    payload = json.loads(result.stdout.decode("utf-8-sig"))
    items = payload.get("words")
    if not isinstance(items, list) or len(items) > MAX_WORDS:
        raise RuntimeError("Local OCR unavailable")
    words = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or len(item["text"]) > 500:
            raise RuntimeError("Invalid local OCR result")
        coordinates = [float(item[key]) for key in ("x", "y", "width", "height")]
        if not all(math.isfinite(value) and 0 <= value <= MAX_DIMENSION for value in coordinates):
            raise RuntimeError("Invalid local OCR result")
        words.append(_Word(item["text"], *coordinates))
    return words


def extract_transcript(data: bytes, *, page_number: int | None = None) -> TranscriptExtraction:
    """Extract one selected page, or a standalone transcript of at most 3 pages."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_PDF_BYTES or not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("Invalid or oversized PDF")
    if page_number is not None and (type(page_number) is not int or not 1 <= page_number <= MAX_PAGES):
        raise ValueError("Invalid PDF page selection")
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        raise ValueError("Invalid or encrypted PDF") from None
    try:
        if pdfium.raw.FPDF_GetSecurityHandlerRevision(document) != -1:
            raise ValueError("Encrypted PDFs are unsupported")
        count = len(document)
        if not 1 <= count <= MAX_PAGES:
            raise ValueError("PDF page limit exceeded")
        if page_number is not None and page_number > count:
            raise ValueError("Invalid PDF page selection")
        if page_number is None and count > MAX_OCR_PAGES:
            return TranscriptExtraction(courses=[], issues=[SELECTION_ISSUE], extraction_method="manual_required")
        indices = [page_number - 1] if page_number is not None else range(count)
        courses: list[ExtractedCourse] = []
        metadata_values: list[tuple[int | None, str | None, int | None]] = []
        issues = [CONFIRMATION_ISSUE]
        used_ocr = False
        for index in indices:
            page = document[index]
            try:
                words = _text_words(page)
                parsed, metadata = _parse_words(words, page_index=index)
                if not parsed:
                    try:
                        words = _windows_ocr(_png(page))
                        used_ocr = True
                        parsed, metadata = _parse_words(words, page_index=index)
                    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
                        if OCR_ISSUE not in issues:
                            issues.append(OCR_ISSUE)
                courses.extend(parsed)
                metadata_values.append(metadata)
            finally:
                page.close()
        if len(courses) > 250:
            courses = courses[:250]
            issues.append(TRUNCATED_ISSUE)
        if not courses:
            issues.append(NO_ROWS_ISSUE)
        elif any(row.credits is None or row.grade is None or row.term is None for row in courses):
            issues.append(INCOMPLETE_ISSUE)
        fields = []
        for position in range(3):
            values = {item[position] for item in metadata_values if item[position] is not None}
            fields.append(next(iter(values)) if len(values) == 1 else None)
        return TranscriptExtraction(
            detected_admission_year=fields[0], detected_department=fields[1],
            reported_earned_credits=fields[2], courses=courses, issues=issues,
            extraction_method="windows_ocr" if used_ocr and courses else "pdf_text" if courses else "manual_required",
        )
    finally:
        document.close()
