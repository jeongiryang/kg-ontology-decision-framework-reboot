"""Identifier-free, explicitly confirmed transcript contracts."""
from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator, model_serializer
from .models import EvidencePacket, Status

Category = Literal["foundation", "balanced", "expanded", "major_required", "major_elective", "free", "unknown"]
Grade = Literal["A+", "A0", "B+", "B0", "C+", "C0", "D+", "D0", "F", "F0", "P", "PASS", "S", "U", "W"]
Area = Literal["digital-communication", "humanities-arts", "society-culture", "nature-science-technology"]
ReviewFlag = Literal["retake", "equivalence", "retroactivity", "recognition_unverified"]
VerificationKind = Literal[
    "record_completeness", "degree_track", "category", "course_identity",
    "duplicate_or_retake", "equivalence", "retroactivity", "balanced_area", "advanced_allocation",
]


class TranscriptCourse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    row_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    course_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9]{2,15}$")
    course_name: str = Field(min_length=1, max_length=100)
    credits: StrictInt = Field(ge=0, le=30)
    grade: Grade
    category: Category
    term: str | None = Field(default=None, pattern=r"^20[0-9]{2}-[12S]$")
    balanced_area: Area | None = None
    excluded: bool = False
    review_flags: list[ReviewFlag] = Field(default_factory=list, max_length=4)

    @field_validator("course_name")
    @classmethod
    def safe_course_name(cls, value):
        value = value.strip()
        if not value or re.search(r"[@<>]|\d{6,}|주민|학번|전화|이메일", value):
            raise ValueError("invalid course label")
        return value


class ExtractedCourse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_id: str
    course_code: str | None = None
    course_name: str
    credits: int | None = None
    grade: Grade | None = None
    category: Category = "unknown"
    term: str | None = None
    balanced_area: Area | None = None
    excluded: bool = False


class TranscriptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    detected_admission_year: int | None = None
    detected_department: Literal["컴퓨터공학과"] | None = None
    courses: list[ExtractedCourse] = Field(max_length=250)
    reported_earned_credits: int | None = None
    issues: list[str]
    requires_confirmation: Literal[True] = True
    extraction_method: Literal["pdf_text", "windows_ocr", "manual_required"]


class TranscriptAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0.0"] = "1.0.0"
    admission_year: StrictInt = Field(ge=1900, le=2200)
    matched_curriculum_year: StrictInt = Field(ge=1900, le=2200)
    department: Literal["컴퓨터공학과"]
    degree_track: Literal["single_major", "minor", "multiple_major", "unknown"] = "unknown"
    confirmed: Literal[True]
    record_complete: bool
    courses: list[TranscriptCourse] = Field(min_length=1, max_length=250)

    @field_validator("confirmed", mode="before")
    @classmethod
    def exact_confirmation(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("explicit boolean confirmation required")
        return value

    @field_validator("courses")
    @classmethod
    def unique_rows(cls, value):
        if len({row.row_id for row in value}) != len(value):
            raise ValueError("duplicate row id")
        return value


class TranscriptEvidencePacket(EvidencePacket):
    """Enforce the existing EvidencePacket status semantics in nested outputs."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"allOf": [
        {"if": {"properties": {"status": {"const": "supported"}}, "required": ["status"]},
         "then": {"properties": {"applied_rules": {"minItems": 1}, "evidence": {"minItems": 1}, "issues": {"maxItems": 0}}}},
        {"if": {"properties": {"status": {"enum": ["insufficient_evidence", "conflict", "out_of_scope"]}}, "required": ["status"]},
         "then": {"properties": {"applied_rules": {"maxItems": 0}, "evidence": {"maxItems": 0}, "issues": {"minItems": 1}}}},
    ]})

    @model_validator(mode="after")
    def status_constraints(self):
        if self.status == "supported":
            if not self.applied_rules or not self.evidence or self.issues:
                raise ValueError("invalid supported evidence packet")
        elif self.applied_rules or self.evidence or not self.issues:
            raise ValueError("invalid unsupported evidence packet")
        return self


class TranscriptCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: str
    label: str
    result: Literal["met", "not_met", "needs_review"]
    required: int | None = None
    earned: int | None = None
    gap: int | None = None
    missing_courses: list[str] = Field(default_factory=list)
    note: str
    evidence_packet: TranscriptEvidencePacket
    policy_packet: TranscriptEvidencePacket | None = None


class TranscriptVerificationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    kind: VerificationKind
    severity: Literal["blocking", "advisory"]
    row_ids: list[str] = Field(default_factory=list)
    check_ids: list[str] = Field(default_factory=list)
    message: str
    action: str


class TranscriptCreditSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_pass_credits: int = Field(ge=0)
    conditional_graduation_credits: int | None = Field(default=None, ge=0)
    unresolved_pass_credits: int = Field(ge=0)
    recognition_status: Literal["partial_comparison", "needs_review"]


class TranscriptAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    status: Status
    conclusion: Literal["approved_requirements_not_met", "needs_review", "out_of_scope"]
    answer: str
    raw_earned_credits: int
    recognized_graduation_credits: int | None
    checks: list[TranscriptCheck]
    issues: list[str]
    verification_items: list[TranscriptVerificationItem] = Field(default_factory=list)
    credit_summary: TranscriptCreditSummary | None = None
    official_graduation_decision: Literal[False] = False


class TranscriptFollowupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0.0"] = "1.0.0"
    question: str = Field(min_length=1, max_length=200)
    transcript: TranscriptAssessmentRequest
    response_style: Literal["friendly"] | None = None
    previous_question: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("previous_question")
    @classmethod
    def trim_previous_question(cls, value):
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("previous question must not be blank")
        return value

    @model_serializer(mode="wrap")
    def compatible_request(self, handler):
        value = handler(self)
        for field in ("response_style", "previous_question"):
            if getattr(self, field) is None:
                value.pop(field, None)
        return value


class TranscriptFollowupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    status: Status
    answer: str
    selected_checks: list[TranscriptCheck]
    focus_check_ids: list[str] = Field(default_factory=list)
    verification_items: list[TranscriptVerificationItem] = Field(default_factory=list)
    conversational_answer: str | None = Field(default=None, min_length=1, max_length=8000)
    context_question: str | None = Field(default=None, min_length=1, max_length=200)
    context_used: StrictBool | None = None

    @model_validator(mode="after")
    def context_boundary(self):
        if self.context_question is not None and (
                self.status != "supported" or not self.selected_checks
                or any(check.evidence_packet.status != "supported" for check in self.selected_checks)):
            raise ValueError("context requires supported selected checks")
        return self

    @model_serializer(mode="wrap")
    def compatible_response(self, handler):
        value = handler(self)
        for field in ("conversational_answer", "context_question", "context_used"):
            if getattr(self, field) is None:
                value.pop(field, None)
        return value
