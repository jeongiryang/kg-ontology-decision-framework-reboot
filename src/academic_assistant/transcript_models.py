"""Identifier-free, explicitly confirmed transcript contracts."""
from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from .models import EvidencePacket, Status

Category = Literal["foundation", "balanced", "expanded", "major_required", "major_elective", "free", "unknown"]
Grade = Literal["A+", "A0", "B+", "B0", "C+", "C0", "D+", "D0", "F", "F0", "P", "PASS", "S", "U", "W"]
Area = Literal["digital-communication", "humanities-arts", "society-culture", "nature-science-technology"]


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
    official_graduation_decision: Literal[False] = False


class TranscriptFollowupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0.0"] = "1.0.0"
    question: str = Field(min_length=1, max_length=200)
    transcript: TranscriptAssessmentRequest


class TranscriptFollowupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    status: Status
    answer: str
    selected_checks: list[TranscriptCheck]
