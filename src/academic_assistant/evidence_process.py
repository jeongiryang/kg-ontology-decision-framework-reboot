"""Bound public evidence rendering without sending the private server environment."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlencode

from .transcript_process import _apply_public_memory_limit, _preview_child_environment, _run_bounded_child


PREVIEW_TIMEOUT_SECONDS = 20
MAX_STDOUT_BYTES = 16 * 1024 * 1024
MAX_INPUT_BYTES = 256 * 1024


def preview_isolated(service, rule_id: str, *, evidence_index: int = 0,
                     pdf_page: int | None = None, render: bool = False,
                     render_format: str = "PNG"):
    from .evidence_pdf import EvidencePreview, EvidenceUnavailable, _FALLBACK, _TABLE_FALLBACK, _TA_FALLBACK, _COURSE_NOTICE

    rule, evidence, source, pages = service._citation(rule_id, evidence_index, pdf_page)
    try:
        if render_format not in {"PNG", "PDF"}:
            raise ValueError("invalid render format")
        path = Path(service.source_map[source["source_id"]])
        if not path.is_absolute() or path.suffix.lower() != ".pdf":
            raise ValueError("invalid source path")
        # Only fields used for approval, source verification and exact redlining
        # cross this boundary. No registry configuration or connection settings.
        subset = {
            "rule": {"rule_id": rule["rule_id"], "review": {key: rule["review"][key]
                                                               for key in ("status", "mode", "scope")},
                     "answer_policy": rule.get("answer_policy"),
                     "applicability": {key: rule.get("applicability", {}).get(key)
                                       for key in ("admission_years", "departments")},
                     "decision": {"outcome": {key: rule.get("decision", {}).get("outcome", {}).get(key)
                                               for key in ("type", "credits")}},
                     "evidence": [{key: evidence[key] for key in
                                   ("source_id", "locator", "excerpt", "evidence_type")}]}
                     if "course_fact" not in rule else None,
            "source": {"source_id": source["source_id"], "sha256": source["sha256"],
                       "review": {key: source["review"][key] for key in ("status", "mode", "scope")}},
            "source_path": str(path), "pdf_page": pdf_page,
            "render": render, "render_format": render_format,
        }
        if "course_fact" in rule:
            # Literal source-verified course data, not a manufactured RuleFact approval.
            subset.pop("rule", None)
            subset["course"] = rule["course_fact"]
        data = json.dumps(subset, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError("input limit")
        output = _run_bounded_child(
            [sys.executable, "-m", "academic_assistant.evidence_process"], data,
            environment=_preview_child_environment(), timeout_seconds=PREVIEW_TIMEOUT_SECONDS,
            output_limit=MAX_STDOUT_BYTES,
        )
        result = json.loads(output)
        if not isinstance(result, dict) or set(result) != {"metadata", "rendered"}:
            raise ValueError("invalid child output")
        metadata = EvidencePreview.model_validate(result["metadata"])
        if (metadata.rule_id != rule_id or metadata.source_id != source["source_id"]
                or metadata.source_sha256 != source["sha256"] or metadata.pdf_page not in pages
                or metadata.printed_page != pages[metadata.pdf_page]
                or (pdf_page is not None and metadata.pdf_page != pdf_page)
                or metadata.quote != evidence["excerpt"] or metadata.location != evidence["locator"]):
            raise ValueError("invalid child citation")
        notices = {"exact": {"승인된 학과·입학연도·열·학점의 교차 위치를 빨간 밑줄로 표시했습니다.",
                             "승인된 인용문과 일치하는 원문 위치를 빨간 밑줄로 표시했습니다.", _COURSE_NOTICE},
                   "page_only": {_FALLBACK, _TABLE_FALLBACK, _TA_FALLBACK}}
        if metadata.notice not in notices[metadata.precision]:
            raise ValueError("invalid child notice")
        query = urlencode({"evidence_index": evidence_index, "pdf_page": metadata.pdf_page})
        metadata.image_url = f"/v1/academic/evidence/{rule_id}/preview.png?{query}"
        metadata.pdf_url = f"/v1/academic/evidence/{rule_id}/preview.pdf?{query}"
        rendered = base64.b64decode(result["rendered"], validate=True) if render else None
        if (not render and result["rendered"] is not None) or (render and not rendered):
            raise ValueError("invalid child rendering")
        if render and not rendered.startswith(b"%PDF-" if render_format == "PDF" else b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid child rendering format")
        return metadata, rendered
    except Exception:
        raise EvidenceUnavailable() from None


def _child_main() -> int:
    # Input is trusted parent JSON, never code, a command line or environment.
    try:
        _apply_public_memory_limit()
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if not data or len(data) > MAX_INPUT_BYTES:
            return 1
        request = json.loads(data)
        common = {"source", "source_path", "pdf_page", "render", "render_format"}
        if set(request) not in (common | {"rule"}, common | {"course"}):
            return 1
        from .evidence_pdf import EvidencePdfService
        source = request["source"]
        rule = request.get("rule")
        identifier = rule["rule_id"] if rule else request["course"]["course_id"]
        service = EvidencePdfService(
            SimpleNamespace(rules={identifier: rule} if rule else {}, sources={source["source_id"]: source},
                            catalogue={"courses": [request["course"]]} if not rule else None),
            {source["source_id"]: request["source_path"]},
        )
        metadata, rendered = service.preview(identifier, pdf_page=request["pdf_page"],
                                             render=request["render"], render_format=request["render_format"])
        result = {"metadata": metadata.model_dump(mode="json"),
                  "rendered": base64.b64encode(rendered).decode("ascii") if rendered is not None else None}
        output = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(output) > MAX_STDOUT_BYTES:
            return 1
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(_child_main())
