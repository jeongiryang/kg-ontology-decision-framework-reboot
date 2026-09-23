from __future__ import annotations

import json
import os
import re
import secrets
import threading
import unicodedata
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import validate_public_text_safety
from .models import AcademicFeedbackRequest, AcademicFeedbackResponse

_WRITE_LOCK = threading.Lock()
_STATUSES = ("insufficient_evidence", "conflict")
_CATEGORIES = ("missing_evidence", "unclear_question", "scope_request", "other")
_RECORD_KEYS = {
    "schema_version",
    "feedback_id",
    "received_at",
    "scope",
    "packet_id",
    "status",
    "question",
    "category",
}
_SCOPE = {
    "admission_year": 2026,
    "matched_curriculum_year": 2026,
    "department": "컴퓨터공학과",
}
_KOREAN_NAME_WITH_PARTICLE = re.compile(
    r"(?<![가-힣])"
    r"(?P<name>(?:김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권|황|안|송|전|홍|유|고|문|양|손|배|백|허|남|심|노|하|곽|성|차|주|우|구|민|진|지|엄|채|원|천|방|공|현|함|변|염|여|추|도|소|석|선|설|마|길|연|위|표|명|기|반|왕|금|옥|육|인|맹|제|모|탁|국|어|은|편|용)[가-힣]{2})"
    r"(?:에게|한테|의|은|는|이|가|을|를)"
    r"(?![가-힣])"
)
_ACADEMIC_THREE_SYLLABLE_TERMS = frozenset({"공모전", "장학금", "원전공", "한과목", "한학기", "한학점"})


class FeedbackSummaryError(ValueError):
    """Raised without record content when a local feedback file is not trustworthy."""

    def __init__(self) -> None:
        super().__init__("invalid feedback file")


@dataclass(frozen=True)
class FeedbackSummary:
    record_count: int
    status_counts: dict[str, int]
    category_counts: dict[str, int]
    duplicate_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "record_count": self.record_count,
            "status_counts": self.status_counts,
            "category_counts": self.category_counts,
            "duplicate_count": self.duplicate_count,
        }


def feedback_path() -> Path:
    configured = os.environ.get("ACADEMIC_FEEDBACK_PATH")
    return Path(configured) if configured else Path(".local/academic-feedback/feedback.jsonl")


def feedback_private_root() -> Path:
    configured = os.environ.get("ACADEMIC_FEEDBACK_PRIVATE_ROOT")
    return Path(configured) if configured else Path(".local/academic-feedback")


def _approved_feedback_destination() -> Path:
    destination = feedback_path().resolve(strict=False)
    private_root = feedback_private_root().resolve(strict=False)
    try:
        relative = destination.relative_to(private_root)
    except ValueError:
        raise OSError("feedback destination is outside the private root") from None
    if not relative.parts:
        raise OSError("feedback destination must be a file below the private root")
    return destination


def _validate_feedback_question_safety(question: str) -> None:
    normalized = unicodedata.normalize("NFKC", question)
    validate_public_text_safety(normalized)
    for match in _KOREAN_NAME_WITH_PARTICLE.finditer(normalized):
        candidate = match.group("name")
        if not candidate.endswith(("생", "자")) and candidate not in _ACADEMIC_THREE_SYLLABLE_TERMS:
            raise ValueError("unsafe or identifying question content")


def store_feedback(request: AcademicFeedbackRequest) -> AcademicFeedbackResponse:
    _validate_feedback_question_safety(request.question)
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
    destination = _approved_feedback_destination()
    with _WRITE_LOCK:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return AcademicFeedbackResponse(feedback_id=feedback_id)


def _validated_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RECORD_KEYS:
        raise FeedbackSummaryError()
    if value.get("schema_version") != "1.0.0":
        raise FeedbackSummaryError()
    feedback_id = value.get("feedback_id")
    packet_id = value.get("packet_id")
    if (
        not isinstance(feedback_id, str)
        or len(feedback_id) != 33
        or not feedback_id.startswith("feedback-")
        or any(character not in "0123456789abcdef" for character in feedback_id[9:])
        or not isinstance(packet_id, str)
        or len(packet_id) != 41
        or not packet_id.startswith("academic-")
        or any(character not in "0123456789abcdef" for character in packet_id[9:])
    ):
        raise FeedbackSummaryError()
    received_at = value.get("received_at")
    if not isinstance(received_at, str):
        raise FeedbackSummaryError()
    try:
        parsed_at = datetime.fromisoformat(received_at)
    except ValueError:
        raise FeedbackSummaryError() from None
    if (
        parsed_at.tzinfo is None
        or parsed_at.utcoffset() != UTC.utcoffset(parsed_at)
        or parsed_at.isoformat(timespec="seconds") != received_at
    ):
        raise FeedbackSummaryError()
    if value.get("scope") != _SCOPE:
        raise FeedbackSummaryError()
    status = value.get("status")
    category = value.get("category")
    question = value.get("question")
    if status not in _STATUSES or category not in _CATEGORIES:
        raise FeedbackSummaryError()
    if not isinstance(question, str) or question != question.strip() or not 1 <= len(question) <= 500:
        raise FeedbackSummaryError()
    try:
        _validate_feedback_question_safety(question)
    except ValueError:
        raise FeedbackSummaryError() from None
    return value


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise FeedbackSummaryError()
        value[key] = item
    return value


def _reject_nonstandard_json_constant(_value: str) -> None:
    raise FeedbackSummaryError()


def summarize_feedback(path: Path | None = None) -> FeedbackSummary:
    source = path if path is not None else feedback_path()
    if not source.exists():
        return FeedbackSummary(
            record_count=0,
            status_counts={status: 0 for status in _STATUSES},
            category_counts={category: 0 for category in _CATEGORIES},
            duplicate_count=0,
        )
    try:
        raw = source.read_bytes()
        lines = raw.decode("utf-8").splitlines()
        records = []
        for line in lines:
            if not line.strip():
                raise FeedbackSummaryError()
            records.append(_validated_record(json.loads(
                line,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_nonstandard_json_constant,
            )))
    except FeedbackSummaryError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise FeedbackSummaryError() from None

    status_counts = {status: 0 for status in _STATUSES}
    category_counts = {category: 0 for category in _CATEGORIES}
    identities: set[tuple[str, str, str, str]] = set()
    duplicate_count = 0
    for record in records:
        status_counts[record["status"]] += 1
        category_counts[record["category"]] += 1
        identity = (record["packet_id"], record["status"], record["question"], record["category"])
        if identity in identities:
            duplicate_count += 1
        identities.add(identity)
    return FeedbackSummary(
        record_count=len(records),
        status_counts=status_counts,
        category_counts=category_counts,
        duplicate_count=duplicate_count,
    )
