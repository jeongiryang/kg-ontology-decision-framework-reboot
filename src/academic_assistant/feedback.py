from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path

from .core import validate_public_text_safety
from .models import AcademicFeedbackRequest, AcademicFeedbackResponse

_WRITE_LOCK = threading.Lock()


def feedback_path() -> Path:
    configured = os.environ.get("ACADEMIC_FEEDBACK_PATH")
    return Path(configured) if configured else Path(".local/academic-feedback/feedback.jsonl")


def store_feedback(request: AcademicFeedbackRequest) -> AcademicFeedbackResponse:
    validate_public_text_safety(request.question)
    received_at = datetime.now(UTC).isoformat(timespec="seconds")
    identity = {
        "packet_id": request.packet_id,
        "status": request.status,
        "question": request.question,
        "category": request.category,
        "received_at": received_at,
    }
    feedback_id = "feedback-" + secrets.token_hex(12)
    record = {
        "schema_version": "1.0.0",
        "feedback_id": feedback_id,
        "received_at": received_at,
        "scope": {
            "admission_year": 2026,
            "matched_curriculum_year": 2026,
            "department": "컴퓨터공학과",
        },
        **identity,
    }
    destination = feedback_path()
    with _WRITE_LOCK:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return AcademicFeedbackResponse(feedback_id=feedback_id)
