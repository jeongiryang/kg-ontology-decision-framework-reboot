"""One-click demos exercise the real PDF extraction, then trusted fixture metadata."""
from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from .transcript_models import TranscriptAssessmentRequest
from .transcript_assessment import TranscriptAssessor
from .transcript_process import extract_isolated

EXAMPLE_IDS = {"early", "near-graduation", "retake"}


def example_pdf(identifier: str) -> tuple[dict, bytes]:
    if identifier not in EXAMPLE_IDS:
        raise KeyError("unknown synthetic example")
    assets = files("academic_assistant").joinpath("web", "examples")
    manifest = json.loads(assets.joinpath("manifest.json").read_bytes())
    fixture = manifest["examples"][identifier]
    data = assets.joinpath(f"{identifier}.pdf").read_bytes()
    if not manifest["synthetic"] or hashlib.sha256(data).hexdigest() != fixture["pdf_sha256"]:
        raise ValueError("synthetic fixture integrity failure")
    return fixture, data


def recognize_example(engine, identifier: str) -> dict:
    fixture, data = example_pdf(identifier)
    extraction = extract_isolated(data, page_number=1)
    fields = ("course_code", "course_name", "credits", "grade", "category")
    expected = fixture["rows"]
    if extraction.extraction_method != "pdf_text" or len(extraction.courses) != len(expected):
        raise ValueError("synthetic PDF recognition failed")
    rows = []
    for parsed, original in zip(extraction.courses, expected):
        # Metadata cannot conceal a bad PDF recognizer or supply missing cells.
        if any(getattr(parsed, key) != original.get(key) for key in fields):
            raise ValueError("synthetic PDF row mismatch")
        rows.append({**parsed.model_dump(), "balanced_area": original.get("balanced_area"),
                     "excluded": original.get("excluded", False), "review_flags": []})
    request = TranscriptAssessmentRequest.model_validate({"admission_year": 2026, "matched_curriculum_year": 2026,
        "department": "컴퓨터공학과", "degree_track": "single_major", "confirmed": True,
        "record_complete": True, "courses": rows})
    assessment = TranscriptAssessor(engine).assess(request)
    if assessment.raw_earned_credits != fixture["input_pass_credits"]:
        raise ValueError("synthetic comparison mismatch")
    return {"synthetic": True, "example_id": identifier, "title": fixture["title"],
            "pdf_sha256": fixture["pdf_sha256"], "extraction": extraction.model_dump(mode="json"),
            "transcript": request.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json")}
