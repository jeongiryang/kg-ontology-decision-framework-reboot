"""Additive semantic-dialogue contracts; legacy deterministic APIs remain stable."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import AcademicAnswerRequest, Calculation, EvidencePacket, Issue, Scope, Status
from .transcript_models import TranscriptAssessmentRequest


class AssistantTurnRequest(AcademicAnswerRequest):
    previous_question: str | None = Field(default=None, min_length=1, max_length=500)
    transcript: TranscriptAssessmentRequest | None = None


class CourseFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    course_id: str
    course_code: str
    course_name: str
    category: Literal["major_required", "major_elective"]
    credits: int = Field(ge=0, le=30)
    year: int | None = Field(ge=1, le=4)
    semester: int | None = Field(ge=1, le=2)
    offering_years: list[int] = Field(default_factory=list, max_length=4)
    offering_semesters: list[str] = Field(default_factory=list, max_length=3)
    offering_label: str = ""
    theory: int | None = Field(default=None, ge=0, le=30)
    practical: int | None = Field(default=None, ge=0, le=30)
    minor_required: bool = False
    source_id: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    locator: str
    fact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CourseCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    locator: str
    claim: str
    course_id: str


class CourseEvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    packet_id: str
    scope: Scope
    status: Status
    courses: list[CourseFact]
    evidence: list[CourseCitation]
    issues: list[Issue]

    @model_validator(mode="after")
    def coverage(self):
        if self.status == "supported":
            if not self.courses or self.issues or len(self.courses) != len(self.evidence):
                raise ValueError("course evidence coverage missing")
            for fact, cite in zip(self.courses, self.evidence, strict=True):
                if (fact.course_id, fact.source_id, fact.source_sha256, fact.locator) != (cite.course_id, cite.source_id, cite.source_sha256, cite.locator):
                    raise ValueError("course citation mismatch")
        elif self.courses or self.evidence or not self.issues:
            raise ValueError("unsupported course packet contains facts")
        return self


class AssistantPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=16000)
    status: Status
    evidence_packet: EvidencePacket | None = None
    course_evidence: CourseEvidencePacket | None = None
    calculations: list[Calculation] = Field(default_factory=list)

    @model_validator(mode="after")
    def supported_evidence(self):
        packet = self.evidence_packet or self.course_evidence
        if self.evidence_packet is not None and self.course_evidence is not None:
            raise ValueError("one evidence family per part")
        if self.status == "supported" and (packet is None or packet.status != "supported"):
            raise ValueError("supported academic part requires evidence")
        if packet is not None and packet.status != self.status:
            raise ValueError("part status differs from evidence")
        return self


class AssistantTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    packet_id: str
    status: Status
    kind: Literal["academic", "greeting", "clarification", "refusal"]
    answer: str = Field(min_length=1, max_length=32000)
    plan_status: Literal["generated", "cached", "unavailable", "rejected", "fallback"]
    generation_status: Literal["generated", "cached", "fallback", "not_applicable"]
    parts: list[AssistantPart] = Field(default_factory=list, max_length=8)
    context_question: str | None = None
    context_used: bool = False
    reason_code: Literal["processing_unavailable", "no_matching_evidence", "missing_student_input", "pending_review", "unsupported_scope", "ambiguous"] | None = None

    @model_validator(mode="after")
    def aggregate(self):
        if self.status == "supported" and (self.kind != "academic" or not self.parts or any(p.status != "supported" for p in self.parts)):
            raise ValueError("overall support requires every academic part")
        if self.kind == "greeting" and self.parts:
            raise ValueError("greeting cannot claim academic evidence")
        return self
