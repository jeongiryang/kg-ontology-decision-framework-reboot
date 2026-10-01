"""Read-only previews of approved, hash-verified PDF citations.

Only registry rule IDs and their cited pages are request inputs. Source paths
come from a private operator map; PDF bytes and image overlays stay in memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
from threading import BoundedSemaphore, RLock
from typing import Callable, Literal, Mapping
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
import pypdfium2 as pdfium

from .registry import Registry


_PDF_LOCK = RLock()  # PDFium is not thread-safe, including across documents.
_PREVIEW_SLOT = BoundedSemaphore(1)
_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_MAP_BYTES = 64 * 1024
_MAX_PIXELS = 8_000_000
_MAX_DIMENSION = 3000
_CURRICULUM_ID = "cwnu.curriculum.2026.changwon-undergraduate"
_CURRICULUM_SHA = "0d800318dc367a89518f9be6be8f559092474bf00b3573e14bd37b6c44b54471"
_CREDIT_HEADERS = {"졸업학점", "기초교양", "균형교양", "전공필수", "심화전공"}
_FALLBACK = "인용문의 정확한 위치를 유일하게 검증하지 못했습니다. 승인된 근거 페이지를 표시하며, 빨간 밑줄은 제공하지 않습니다."
_TABLE_FALLBACK = "표의 행·열과 학점의 교차 위치를 유일하게 검증하지 못했습니다. 승인된 근거 페이지를 표시하며, 빨간 밑줄은 제공하지 않습니다."
_TA_FALLBACK = "조교 확인 문서는 수기 내용의 정확한 위치를 자동 검증할 수 없어 승인된 근거 페이지만 표시합니다."


class EvidenceUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("evidence preview unavailable")


class EvidenceNotFound(LookupError):
    def __init__(self):
        super().__init__("evidence not found")


class EvidencePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,127}$")
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,127}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_page: int = Field(ge=1)
    printed_page: int | None = Field(ge=1)
    quote: str = Field(min_length=1)
    location: str = Field(min_length=1)
    precision: Literal["exact", "page_only"]
    notice: str = Field(min_length=1)
    image_url: str
    pdf_url: str


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate source map key")
        result[key] = value
    return result


def _approved(value: dict) -> bool:
    review = value.get("review", {})
    return (review.get("status"), review.get("mode"), review.get("scope")) == ("approved", "human", "full")


def _cited_pages(locator: str) -> dict[int, int | None]:
    """Parse PDF references, excluding their parenthesized printed-page labels."""
    pages: dict[int, int | None] = {}
    in_pdf = False
    for match in re.finditer(r"(?:(PDF)\s+)?pp?\.\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*(?:\(([^)]*)\))?", locator, re.I):
        if match[1]:
            in_pdf = True
        if not in_pdf:
            continue
        first = int(match[2]); last = int(match[3] or first)
        if not 1 <= first <= last <= 10_000 or last - first > 20:
            raise EvidenceUnavailable()
        printed = re.search(r"(?:printed|인쇄)\s*pp?\.\s*(\d+)(?:\s*[-–]\s*(\d+))?", match[4] or "", re.I)
        for page in range(first, last + 1):
            printed_page = int(printed[1]) + page - first if printed else None
            if printed and page > first and (not printed[2] or printed_page > int(printed[2])):
                printed_page = None
            pages.setdefault(page, printed_page)
    if not pages:
        raise EvidenceNotFound()
    return pages


def _compact(text: str) -> str:
    return "".join(text.split())


@dataclass(frozen=True)
class _Span:
    rects: tuple[tuple[float, float, float, float], ...]

    @property
    def box(self):
        return (min(r[0] for r in self.rects), min(r[1] for r in self.rects),
                max(r[2] for r in self.rects), max(r[3] for r in self.rects))


def _matches(textpage, needle: str, *, numeric: bool = False) -> list[_Span]:
    if not needle or len(needle) > 2000:
        return []
    spans = []
    with closing(textpage.search(needle, match_case=True, consecutive=True)) as search:
        while (match := search.get_next()) is not None:
            if len(spans) >= 256:
                return []
            start, count = match
            actual = textpage.get_text_range(start, count)
            if _compact(actual) != _compact(needle) or (numeric and actual != needle):
                continue
            if numeric:
                before = textpage.get_text_range(start - 1, 1) if start else ""
                after = textpage.get_text_range(start + count, 1) if start + count < textpage.count_chars() else ""
                if any(char.isalnum() or char in ".+-" for char in before + after):
                    continue
            # PDFium groups actual text rectangles correctly even when glyph
            # ink heights differ (Latin descenders, vertically split headers).
            textpage.count_rects()
            boxes = [textpage.get_rect(i) for i in range(textpage.count_rects(start, count))]
            boxes = [box for box in boxes if all(math.isfinite(n) for n in box)
                     and box[2] > box[0] and box[3] > box[1]]
            if not boxes:
                continue
            spans.append(_Span(tuple(boxes)))
    return spans


def _cell(spans: list[_Span], row: _Span, column: _Span) -> _Span | None:
    row_box = row.box; column_box = column.box
    candidates = []
    for span in spans:
        box = span.box
        x = (box[0] + box[2]) / 2; y = (box[1] + box[3]) / 2
        if (column_box[0] - 1 <= x <= column_box[2] + 1
                and row_box[1] - 1 <= y <= row_box[3] + 1
                and len(span.rects) == 1):
            candidates.append(span)
    return candidates[0] if len(candidates) == 1 else None


def _table_rects(textpage, *, row_label: str, year: int, header_label: str, value: int,
                 year_header_label: str = "입학년도"):
    """Require unique row/header/year and exactly one value at the intersection."""
    rows = _matches(textpage, row_label)
    headers = _matches(textpage, header_label)
    year_headers = _matches(textpage, year_header_label)
    if len(rows) != 1 or len(headers) != 1 or len(year_headers) != 1 or len(rows[0].rects) != 1:
        return []
    row, header, year_header = rows[0], headers[0], year_headers[0]
    if header.box[1] <= row.box[3] or year_header.box[1] <= row.box[3]:
        return []
    year_cell = _cell(_matches(textpage, str(year), numeric=True), row, year_header)
    value_cell = _cell(_matches(textpage, str(value), numeric=True), row, header)
    if year_cell is None or value_cell is None:
        return []
    return [rect for span in (row, year_header, year_cell, header, value_cell) for rect in span.rects]


def _citation_rects(textpage, rule: dict, evidence: dict, source: dict, page_number: int):
    if evidence["evidence_type"] == "department_confirmation":
        return [], _TA_FALLBACK
    if textpage.count_chars() > 100_000:
        return [], _FALLBACK
    quote = evidence["excerpt"]
    if evidence["evidence_type"] == "table_structure":
        # This row abbreviation is verified against the immutable original,
        # not inferred from a program type or an arbitrary duplicate number.
        match = re.fullmatch(r"([가-힣]+)\s+(\d{1,3})", quote)
        outcome = rule["decision"]["outcome"]
        if (source["source_id"] == _CURRICULUM_ID and source["sha256"] == _CURRICULUM_SHA
                and page_number == 577 and match and match[1] in _CREDIT_HEADERS
                and rule["applicability"].get("admission_years") == [2026]
                and rule["applicability"].get("departments") == ["컴퓨터공학과"]
                and outcome.get("type") == "credit_threshold" and outcome.get("credits") == int(match[2])):
            rects = _table_rects(textpage, row_label="컴퓨터공", year=2026,
                                 header_label=match[1], value=int(match[2]))
            if rects:
                return rects, "승인된 학과·입학연도·열·학점의 교차 위치를 빨간 밑줄로 표시했습니다."
        return [], _TABLE_FALLBACK
    if len(_compact(quote)) >= 4 and re.search(r"[가-힣A-Za-z]", quote):
        matches = _matches(textpage, quote)
        if len(matches) == 1:
            return list(matches[0].rects), "승인된 인용문과 일치하는 원문 위치를 빨간 밑줄로 표시했습니다."
    return [], _FALLBACK


def _render_page(page, rects, output_format: Literal["PNG", "PDF"]) -> bytes:
    from PIL import ImageDraw

    width, height = page.get_size()
    if not all(math.isfinite(n) and n > 0 for n in (width, height)):
        raise EvidenceUnavailable()
    scale = min(2.0, _MAX_DIMENSION / max(width, height), math.sqrt(_MAX_PIXELS / (width * height)))
    with closing(page.render(scale=scale)) as bitmap:
        image = bitmap.to_pil().convert("RGB")
        try:
            conversion = bitmap.get_posconv(page)
            draw = ImageDraw.Draw(image)
            for left, bottom, right, _top in rects:
                start = conversion.to_bitmap(left, bottom - 1)
                end = conversion.to_bitmap(right, bottom - 1)
                draw.line((start, end), fill=(220, 25, 25), width=max(2, round(scale * 1.5)))
            output = BytesIO()
            if output_format == "PDF":
                # A flattened, single-page preview copy; never write the source.
                image.save(output, format="PDF", resolution=72 * scale)
            else:
                image.save(output, format="PNG")
            return output.getvalue()
        finally:
            image.close()


class EvidencePdfService:
    def __init__(self, registry: Registry, source_map: Mapping[str, str]):
        self.registry = registry
        self.source_map = dict(source_map)

    @classmethod
    def from_env(cls, registry: Registry, environ: Mapping[str, str] | None = None):
        try:
            env = os.environ if environ is None else environ
            mapping_path = Path(env.get("ACADEMIC_SOURCE_MAP", ""))
            if not mapping_path.is_absolute() or not mapping_path.is_file():
                raise ValueError("invalid source configuration")
            with mapping_path.open("rb") as stream:
                raw = stream.read(_MAX_MAP_BYTES + 1)
            if len(raw) > _MAX_MAP_BYTES:
                raise ValueError("oversized source configuration")
            mapping = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
            if not isinstance(mapping, dict) or not mapping or not set(mapping) <= set(registry.sources):
                raise ValueError("invalid source configuration")
            for path in mapping.values():
                if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).suffix.lower() != ".pdf":
                    raise ValueError("invalid source configuration")
            return cls(registry, mapping)
        except Exception:
            raise EvidenceUnavailable() from None

    def _citation(self, rule_id: str, evidence_index: int, pdf_page: int | None):
        """Resolve approved inputs without reading or parsing the original PDF."""
        rule = self.registry.rules.get(rule_id)
        if rule is None or not _approved(rule) or rule.get("answer_policy") == "record_only":
            raise EvidenceNotFound()
        if not 0 <= evidence_index < len(rule["evidence"]):
            raise EvidenceNotFound()
        evidence = rule["evidence"][evidence_index]
        source = self.registry.sources.get(evidence["source_id"])
        if source is None or not _approved(source):
            raise EvidenceNotFound()
        pages = _cited_pages(evidence["locator"])
        if pdf_page is not None and pdf_page not in pages:
            raise EvidenceNotFound()
        return rule, evidence, source, pages

    def preview(self, rule_id: str, *, evidence_index: int = 0, pdf_page: int | None = None,
                render: bool = False, render_format: Literal["PNG", "PDF"] = "PNG") -> tuple[EvidencePreview, bytes | None]:
        rule, evidence, source, pages = self._citation(rule_id, evidence_index, pdf_page)
        try:
            path = Path(self.source_map[source["source_id"]])
            if not path.is_absolute() or path.suffix.lower() != ".pdf" or not path.is_file():
                raise EvidenceUnavailable()
            with path.open("rb") as stream:
                data = stream.read(_MAX_SOURCE_BYTES + 1)
            if (len(data) > _MAX_SOURCE_BYTES or not data.startswith(b"%PDF-")
                    or hashlib.sha256(data).hexdigest() != source["sha256"]):
                raise EvidenceUnavailable()
            with _PDF_LOCK, closing(pdfium.PdfDocument(data)) as document:
                selected = pdf_page or next(iter(pages))
                rects = []; notice = _FALLBACK
                for candidate in ([pdf_page] if pdf_page is not None else pages):
                    if not 1 <= candidate <= len(document):
                        raise EvidenceUnavailable()
                    with closing(document[candidate - 1]) as page, closing(page.get_textpage()) as textpage:
                        candidate_rects, candidate_notice = _citation_rects(textpage, rule, evidence, source, candidate)
                    if candidate == selected or candidate_rects:
                        selected, rects, notice = candidate, candidate_rects, candidate_notice
                    if rects:
                        break
                png = None
                if render:
                    with closing(document[selected - 1]) as page:
                        png = _render_page(page, rects, render_format)
            query = urlencode({"evidence_index": evidence_index, "pdf_page": selected})
            metadata = EvidencePreview(rule_id=rule_id, source_id=source["source_id"],
                source_sha256=source["sha256"], pdf_page=selected, printed_page=pages[selected],
                quote=evidence["excerpt"], location=evidence["locator"], precision="exact" if rects else "page_only",
                notice=notice, image_url=f"/v1/academic/evidence/{rule_id}/preview.png?{query}",
                pdf_url=f"/v1/academic/evidence/{rule_id}/preview.pdf?{query}")
            return metadata, png
        except EvidenceNotFound:
            raise
        except Exception:
            raise EvidenceUnavailable() from None


def create_evidence_router(engine_provider: Callable, *, environ: Mapping[str, str] | None = None) -> APIRouter:
    """Inject the existing engine factory without importing the main API module."""
    router = APIRouter()

    def preview(rule_id, evidence_index, pdf_page, render, render_format="PNG"):
        if not _PREVIEW_SLOT.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="evidence preview busy")
        try:
            engine = engine_provider()
            registry = engine.registry
            # Unknown IDs fail independently of private source configuration.
            if rule_id not in registry.rules:
                raise EvidenceNotFound()
            reader = getattr(engine, "evidence_reader", None)
            if reader is not None:
                reader.verify(registry)
            service = EvidencePdfService.from_env(registry, environ)
            env = os.environ if environ is None else environ
            if env.get("ACADEMIC_PUBLIC_DEMO") == "1":
                from .evidence_process import preview_isolated
                return preview_isolated(service, rule_id, evidence_index=evidence_index,
                                        pdf_page=pdf_page, render=render, render_format=render_format)
            return service.preview(rule_id, evidence_index=evidence_index, pdf_page=pdf_page,
                                   render=render, render_format=render_format)
        except EvidenceNotFound:
            raise HTTPException(status_code=404, detail="evidence not found") from None
        except Exception:
            raise HTTPException(status_code=503, detail="evidence preview unavailable") from None
        finally:
            _PREVIEW_SLOT.release()

    @router.get("/v1/academic/evidence/{rule_id}/preview", response_model=EvidencePreview)
    def metadata(rule_id: str, evidence_index: int = 0, pdf_page: int | None = None):
        return preview(rule_id, evidence_index, pdf_page, False)[0]

    @router.get("/v1/academic/evidence/{rule_id}/preview.png")
    def image(rule_id: str, evidence_index: int = 0, pdf_page: int | None = None):
        info, png = preview(rule_id, evidence_index, pdf_page, True)
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "X-Evidence-Precision": info.precision})

    @router.get("/v1/academic/evidence/{rule_id}/preview.pdf")
    def download(rule_id: str, evidence_index: int = 0, pdf_page: int | None = None):
        info, pdf = preview(rule_id, evidence_index, pdf_page, True, "PDF")
        return Response(pdf, media_type="application/pdf", headers={"Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "X-Evidence-Precision": info.precision,
            "Content-Disposition": f'attachment; filename="citation-{info.rule_id}-p{info.pdf_page}.pdf"'})

    return router
