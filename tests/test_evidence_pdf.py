"""Boundary tests use small generated PDFs, never private source documents."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
import pypdfium2 as pdfium

from academic_assistant.evidence_pdf import (
    EvidenceNotFound, EvidencePdfService, EvidencePreview, EvidenceUnavailable,
    _cited_pages, _table_rects, create_evidence_router,
)


RULE = "fixture.approved.rule"
SOURCE = "fixture.approved.source"
REVIEW = {"status": "approved", "mode": "human", "scope": "full"}


def pdf_bytes(pages, *, rotation=0):
    """Minimal text PDFs with valid xref offsets and selectable glyph boxes."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_refs = []
    for entries in pages:
        page_id = len(objects) + 1; stream_id = page_id + 1
        page_refs.append(f"{page_id} 0 R")
        content = []
        for text, x, y in entries:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content.append(f"BT /F1 12 Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET")
        stream = "\n".join(content).encode("ascii")
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 750] /Rotate {rotation} "
                        f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>").encode("ascii"))
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(page_refs)}] >>".encode()
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result)); result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(result)


def registry_for(data, *, quote="Approved graduation requirement", locator="PDF p.1 (printed p.9)", kind="explicit_text"):
    rule = {"rule_id": RULE, "review": copy.deepcopy(REVIEW), "applicability": {}, "decision": {"outcome": {}},
            "evidence": [{"source_id": SOURCE, "locator": locator, "excerpt": quote, "evidence_type": kind}]}
    source = {"source_id": SOURCE, "review": copy.deepcopy(REVIEW), "sha256": hashlib.sha256(data).hexdigest()}
    return SimpleNamespace(rules={RULE: rule}, sources={SOURCE: source})


def red_pixels(png):
    with Image.open(BytesIO(png)) as image:
        return [(i % image.width, i // image.width) for i, (r, g, b) in enumerate(image.convert("RGB").get_flattened_data())
                if r > 150 and g < 80 and b < 80]


class EvidencePdfTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "approved.pdf"
        self.mapping = self.root / "source-map.json"
        self.data = pdf_bytes([[('Approved graduation requirement', 50, 650)]])
        self.source.write_bytes(self.data)
        self.registry = registry_for(self.data)
        self.mapping.write_text(json.dumps({SOURCE: str(self.source)}), encoding="utf-8")
        self.env = {"ACADEMIC_SOURCE_MAP": str(self.mapping)}

    def service(self):
        return EvidencePdfService.from_env(self.registry, self.env)

    def client(self, environ=None, provider=None):
        app = FastAPI()
        app.include_router(create_evidence_router(provider or (lambda: SimpleNamespace(registry=self.registry)),
                                                 environ=self.env if environ is None else environ))
        return TestClient(app)

    def url(self, suffix="preview"):
        return f"/v1/academic/evidence/{RULE}/{suffix}"

    def test_exact_quote_metadata_png_alignment_and_original_preserved(self):
        metadata, png = self.service().preview(RULE, render=True)
        self.assertEqual("exact", metadata.precision)
        self.assertEqual((1, 9), (metadata.pdf_page, metadata.printed_page))
        self.assertEqual(self.registry.sources[SOURCE]["sha256"], metadata.source_sha256)
        self.assertEqual("Approved graduation requirement", metadata.quote)
        pixels = red_pixels(png)
        self.assertTrue(pixels)
        self.assertTrue(all(95 <= y <= 215 and 90 <= x <= 510 for x, y in pixels))
        self.assertEqual(self.data, self.source.read_bytes())
        client = self.client()
        response = client.get(self.url())
        self.assertEqual(200, response.status_code)
        self.assertEqual({"schema_version", "rule_id", "source_id", "source_sha256", "pdf_page", "printed_page", "quote", "location", "precision", "notice", "image_url", "pdf_url"}, set(response.json()))
        image = client.get(response.json()["image_url"])
        self.assertEqual(200, image.status_code)
        self.assertEqual("image/png", image.headers["content-type"])
        self.assertEqual("exact", image.headers["x-evidence-precision"])
        self.assertEqual("no-store", image.headers["cache-control"])
        self.assertNotIn(str(self.root), response.text)

    def test_duplicate_quote_is_page_only_and_no_red_marks(self):
        data = pdf_bytes([[('Approved graduation requirement', 50, 650), ('Approved graduation requirement', 50, 600)]])
        self.source.write_bytes(data); self.registry = registry_for(data)
        metadata, png = self.service().preview(RULE, render=True)
        self.assertEqual("page_only", metadata.precision)
        self.assertTrue(metadata.notice)
        self.assertEqual([], red_pixels(png))

    def test_scanned_or_empty_page_falls_back(self):
        data = pdf_bytes([[]]); self.source.write_bytes(data); self.registry = registry_for(data)
        metadata, png = self.service().preview(RULE, render=True)
        self.assertEqual("page_only", metadata.precision)
        self.assertEqual([], red_pixels(png))

    def test_bare_duplicate_numbers_are_not_exact_table_evidence(self):
        data = pdf_bytes([[('Graduation 130', 50, 650), ('OtherDepartment 130', 50, 600)]])
        self.source.write_bytes(data); self.registry = registry_for(data, quote="130", kind="table_structure")
        metadata, png = self.service().preview(RULE, render=True)
        self.assertEqual("page_only", metadata.precision)
        self.assertEqual([], red_pixels(png))

    def test_handwritten_confirmation_never_claims_automatic_exact(self):
        self.registry.rules[RULE]["evidence"][0]["evidence_type"] = "department_confirmation"
        metadata, png = self.service().preview(RULE, render=True)
        self.assertEqual("page_only", metadata.precision)
        self.assertEqual([], red_pixels(png))

    def test_uncited_pages_and_invalid_evidence_indices_are_404(self):
        client = self.client()
        for query in ("pdf_page=2", "pdf_page=0", "evidence_index=-1", "evidence_index=1"):
            with self.subTest(query=query):
                response = client.get(self.url() + "?" + query)
                self.assertEqual(404, response.status_code)
                self.assertEqual({"detail": "evidence not found"}, response.json())

    def test_unknown_or_traversal_ids_fail_without_configuration(self):
        client = self.client(environ={})
        for unknown in ("unknown", "..%2F..%2Fprivate", "%5Cprivate", "C%3A%5Cprivate.pdf"):
            with self.subTest(unknown=unknown):
                response = client.get(f"/v1/academic/evidence/{unknown}/preview")
                self.assertEqual(404, response.status_code)
        self.assertEqual(503, client.get(self.url()).status_code)

    def test_query_path_cannot_choose_another_source(self):
        private = self.root / "private.pdf"
        private.write_bytes(pdf_bytes([[('PRIVATE CONTENT', 50, 650)]]))
        response = self.client().get(self.url() + "?path=" + str(private))
        self.assertEqual(200, response.status_code)
        self.assertEqual(SOURCE, response.json()["source_id"])
        self.assertNotIn("PRIVATE", response.text)
        self.assertNotIn(str(private), response.text)

    def test_tampering_between_metadata_and_image_fails_closed(self):
        client = self.client()
        metadata = client.get(self.url()).json()
        self.source.write_bytes(self.data + b"tampered")
        response = client.get(metadata["image_url"])
        self.assertEqual(503, response.status_code)
        self.assertEqual({"detail": "evidence preview unavailable"}, response.json())
        self.assertNotIn(str(self.root), response.text)

    def test_mapped_unknown_source_duplicate_key_or_relative_path_rejected(self):
        variants = [{"unapproved.source": str(self.source)}, {SOURCE: "relative.pdf"}, {SOURCE: str(self.root)},
                    {SOURCE: {"path": str(self.source)}}, []]
        client = self.client()
        for value in variants:
            self.mapping.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(503, client.get(self.url()).status_code)
        item = json.dumps(str(self.source))
        self.mapping.write_text('{"' + SOURCE + '":' + item + ',"' + SOURCE + '":' + item + '}', encoding="utf-8")
        self.assertEqual(503, client.get(self.url()).status_code)

    def test_source_missing_invalid_pdf_or_cited_page_beyond_document_is_sanitized(self):
        self.source.unlink()
        self.assertEqual(503, self.client().get(self.url()).status_code)
        invalid = b"%PDF-1.4\ninvalid"; self.source.write_bytes(invalid); self.registry = registry_for(invalid)
        self.assertEqual(503, self.client().get(self.url()).status_code)
        self.source.write_bytes(self.data); self.registry = registry_for(self.data, locator="PDF p.3")
        self.assertEqual(503, self.client().get(self.url()).status_code)

    def test_unapproved_source_rule_and_record_only_are_not_available(self):
        for target in (self.registry.rules[RULE], self.registry.sources[SOURCE]):
            target["review"]["status"] = "pending"
            with self.assertRaises(EvidenceNotFound):
                self.service().preview(RULE)
            target["review"]["status"] = "approved"
        self.registry.rules[RULE]["answer_policy"] = "record_only"
        with self.assertRaises(EvidenceNotFound):
            self.service().preview(RULE)

    def test_secondary_citation_selects_approved_source_and_image_query(self):
        second = "fixture.approved.confirmation"
        self.registry.sources[second] = {**self.registry.sources[SOURCE], "source_id": second}
        self.registry.rules[RULE]["evidence"].append({**self.registry.rules[RULE]["evidence"][0],
            "source_id": second, "evidence_type": "department_confirmation"})
        self.mapping.write_text(json.dumps({SOURCE: str(self.source), second: str(self.source)}), encoding="utf-8")
        metadata = self.client().get(self.url() + "?evidence_index=1").json()
        self.assertEqual(second, metadata["source_id"])
        self.assertEqual("page_only", metadata["precision"])
        self.assertIn("evidence_index=1", metadata["image_url"])

    def test_default_chooses_exact_only_among_cited_pages(self):
        data = pdf_bytes([[], [('Approved graduation requirement', 50, 650)], [('NOT CITED', 50, 650)]])
        self.source.write_bytes(data); self.registry = registry_for(data, locator="PDF pp.1-2 (printed pp.9-10)")
        metadata, _ = self.service().preview(RULE)
        self.assertEqual((2, 10, "exact"), (metadata.pdf_page, metadata.printed_page, metadata.precision))
        metadata, _ = self.service().preview(RULE, pdf_page=1)
        self.assertEqual((1, "page_only"), (metadata.pdf_page, metadata.precision))
        with self.assertRaises(EvidenceNotFound):
            self.service().preview(RULE, pdf_page=3)

    def test_rotated_page_png_renders_red_overlay(self):
        data = pdf_bytes([[('Approved graduation requirement', 50, 650)]], rotation=90)
        self.source.write_bytes(data); self.registry = registry_for(data)
        metadata, png = self.service().preview(RULE, render=True)
        self.assertEqual("exact", metadata.precision)
        self.assertTrue(red_pixels(png))
        with Image.open(BytesIO(png)) as image:
            self.assertEqual((1500, 1200), image.size)

    def test_download_is_marked_single_page_copy_reopens_and_preserves_original(self):
        client = self.client()
        metadata = client.get(self.url()).json()
        response = client.get(metadata["pdf_url"])
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.headers["content-type"])
        self.assertTrue(response.headers["content-disposition"].startswith("attachment;"))
        self.assertTrue(response.content.startswith(b"%PDF-"))
        with closing(pdfium.PdfDocument(response.content)) as document:
            self.assertEqual(1, len(document))
            with closing(document[0]) as page:
                self.assertEqual((600, 750), page.get_size())
                with closing(page.render(scale=2)) as bitmap:
                    with bitmap.to_pil() as image:
                        output = BytesIO(); image.save(output, format="PNG")
                        self.assertTrue(red_pixels(output.getvalue()))
        self.assertEqual(self.data, self.source.read_bytes())

    def test_download_fallback_is_unmarked_page_and_tampering_is_503(self):
        self.registry.rules[RULE]["evidence"][0]["evidence_type"] = "department_confirmation"
        client = self.client()
        response = client.get(self.url("preview.pdf"))
        self.assertEqual(200, response.status_code)
        self.assertEqual("page_only", response.headers["x-evidence-precision"])
        with closing(pdfium.PdfDocument(response.content)) as document:
            self.assertEqual(1, len(document))
            with closing(document[0]) as page, closing(page.render()) as bitmap:
                with bitmap.to_pil() as image:
                    output = BytesIO(); image.save(output, format="PNG")
                    self.assertEqual([], red_pixels(output.getvalue()))
        self.source.write_bytes(self.data + b"tampered")
        self.assertEqual(503, client.get(self.url("preview.pdf")).status_code)

    def test_engine_failure_is_sanitized(self):
        def unavailable():
            raise RuntimeError(str(self.source))
        response = self.client(provider=unavailable).get(self.url())
        self.assertEqual(503, response.status_code)
        self.assertNotIn(str(self.source), response.text)

    def test_metadata_model_rejects_unsupported_precision_versions_and_extra_paths(self):
        metadata = self.service().preview(RULE)[0].model_dump()
        for changes in ({"precision": "approximate"}, {"schema_version": "2.0.0"},
                        {"pdf_page": 0}, {"source_sha256": "unverified"},
                        {"path": str(self.source)}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                EvidencePreview.model_validate({**metadata, **changes})

    def test_active_graph_reader_is_verified_each_preview_and_failure_is_503(self):
        class Reader:
            calls = 0
            fail = False
            def verify(inner, registry):
                inner.calls += 1
                if inner.fail:
                    raise RuntimeError(str(self.source))
        reader = Reader()
        client = self.client(provider=lambda: SimpleNamespace(registry=self.registry, evidence_reader=reader))
        self.assertEqual(200, client.get(self.url()).status_code)
        reader.fail = True
        response = client.get(self.url("preview.png"))
        self.assertEqual(503, response.status_code)
        self.assertEqual(2, reader.calls)
        self.assertNotIn(str(self.source), response.text)

    def test_concurrent_preview_is_429_without_queue_and_slot_recovers(self):
        entered, release = threading.Event(), threading.Event()
        def provider():
            entered.set()
            release.wait(3)
            return SimpleNamespace(registry=self.registry)
        client = self.client(provider=provider)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.get, self.url())
            self.assertTrue(entered.wait(1))
            response = self.client().get(self.url())
            self.assertEqual(429, response.status_code)
            self.assertEqual({"detail": "evidence preview busy"}, response.json())
            release.set()
            self.assertEqual(200, pending.result(3).status_code)
        self.assertEqual(200, self.client().get(self.url()).status_code)

    def test_page_locator_parsing_keeps_printed_pages_separate(self):
        self.assertEqual({23: 15, 261: 253, 577: 569}, _cited_pages("PDF p.23 (printed p.15), p.261 (printed p.253), p.577 (printed p.569): table"))
        self.assertEqual({262: 254, 263: 255}, _cited_pages("PDF pp.262-263 (인쇄 pp.254-255): table"))
        self.assertEqual({2: None, 3: None}, _cited_pages("PDF pp.2-3, section 4"))
        with self.assertRaises(EvidenceNotFound):
            _cited_pages("section 4")
        with self.assertRaises(EvidenceUnavailable):
            _cited_pages("PDF pp.1-500")

    def geometry(self, entries):
        with closing(pdfium.PdfDocument(pdf_bytes([entries]))) as document:
            with closing(document[0]) as page, closing(page.get_textpage()) as textpage:
                return _table_rects(textpage, row_label="ComputerDept", year=2026,
                    header_label="Graduation", value=130, year_header_label="AdmissionYear")

    def table(self):
        return [('AdmissionYear', 160, 500), ('Graduation', 300, 500),
                ('ComputerDept', 40, 400), ('2026', 160, 400), ('130', 300, 400),
                ('OtherDept', 40, 350), ('2026', 160, 350), ('130', 300, 350)]

    def test_duplicate_table_number_is_resolved_by_unique_row_year_and_column(self):
        rects = self.geometry(self.table())
        self.assertEqual(5, len(rects))
        values = [r for r in rects if 299 <= r[0] <= 305 and r[1] < 450]
        self.assertEqual(1, len(values))
        self.assertGreater(values[0][1], 390)

    def test_ambiguous_table_row_header_or_cell_and_wrong_year_fail_closed(self):
        variants = [self.table() + [('ComputerDept', 40, 300)],
                    self.table() + [('Graduation', 400, 500)],
                    self.table() + [('130', 331, 400)],
                    [(text if not (text == '2026' and y == 400) else '2025', x, y) for text, x, y in self.table()]]
        for entries in variants:
            with self.subTest(entries=entries):
                self.assertEqual([], self.geometry(entries))


if __name__ == "__main__":
    unittest.main()
