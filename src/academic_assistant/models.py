from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

Status = Literal["supported", "insufficient_evidence", "conflict", "out_of_scope"]


class AcademicAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0.0"] = "1.0.0"
    question: str = Field(min_length=1, max_length=500)
    admission_year: StrictInt = Field(ge=1900, le=2200)
    matched_curriculum_year: StrictInt = Field(ge=1900, le=2200)
    department: str = Field(min_length=1, max_length=100)
    earned_credits: dict[str, StrictInt] = Field(default_factory=dict)

    @field_validator("question", "department")
    @classmethod
    def trim_text(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must not be blank")
        return trimmed

    @field_validator("earned_credits")
    @classmethod
    def validate_credits(cls, value: dict[str, int]) -> dict[str, int]:
        for metric, credits in value.items():
            if not metric or credits < 0 or credits > 500:
                raise ValueError("invalid earned-credit metric or value")
        return dict(sorted(value.items()))


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admission_year: int
    matched_curriculum_year: int
    department: str


class AppliedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    rule_sha256: str


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    rule_id: str
    locator: str
    claim: str


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["missing", "conflict", "scope", "review"]
    message: str
    related_ids: list[str] | None = None


class EvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0"] = "2.0.0"
    packet_id: str
    scope: Scope
    student_facts: dict[str, int]
    applied_rules: list[AppliedRule]
    evidence: list[EvidenceReference]
    issues: list[Issue]
    status: Status


class Calculation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    required: int
    earned: int
    gap: int


class AcademicAnswerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    packet_id: str
    status: Status
    answer: str
    intent_ids: list[str]
    calculations: list[Calculation]
    evidence_packet: EvidencePacket
