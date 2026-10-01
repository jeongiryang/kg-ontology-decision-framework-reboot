"""Public approved-claim plans and closed-language model output validation.

Neither a request nor a student's answer/calculations are model inputs. The
model may choose wording only from the encodings of approved public claims.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal, Mapping, TYPE_CHECKING

from .llm import LLMBusy, LLMInvalidResponse, LLMUnavailable, VerifiedGeneration
from .models import AcademicAnswerResponse
from .registry import Registry, canonical_sha256

if TYPE_CHECKING:
    from .llm import LocalLLMClient

GenerationStatus = Literal["generated", "cached", "disabled", "unavailable", "rejected", "busy", "not_applicable"]
GRAMMAR_VERSION = "ko-approved-claims-v1"
PUBLIC_SCOPE = {"admission_year": 2026, "matched_curriculum_year": 2026, "department": "컴퓨터공학과"}
INTRODUCTIONS = ("", "확인된 기준을 안내해 드리겠습니다.", "승인된 학사 근거에 따른 안내입니다.")
_SUBJECTS = {
    "credits.graduation.total": "졸업 총학점", "credits.general.total": "교양 총학점",
    "credits.major.total": "전공 총학점", "credits.major.required": "전공필수",
    "credits.major.elective": "전공선택", "credits.general.foundation": "기초교양",
    "credits.general.balanced": "균형교양", "credits.general.remaining": "교양 잔여 학점",
    "credits.major.advanced": "심화전공", "credits.major.minimum": "최소전공",
    "credits.graduation.remaining": "졸업 잔여 학점",
}


class _NotApplicable(ValueError):
    pass


@dataclass(frozen=True)
class GenerationOutcome:
    generation_status: GenerationStatus
    generated_answer: str | None = None
    generated_claim_ids: tuple[str, ...] = ()


def grounded_generation_enabled(environ: Mapping[str, str] | None = None) -> bool:
    value = (os.environ if environ is None else environ).get("ACADEMIC_LLM_GROUNDED_GENERATION", "0")
    if value not in {"0", "1"}:
        raise ValueError("invalid grounded generation setting")
    return value == "1"


def _approved(value: dict) -> bool:
    review = value.get("review", {})
    return (review.get("status"), review.get("mode"), review.get("scope")) == ("approved", "human", "full")


def build_public_plan(base: AcademicAnswerResponse, registry: Registry) -> dict:
    packet = base.evidence_packet
    if base.status != "supported" or packet.status != "supported":
        raise _NotApplicable("generation requires supported evidence")
    if packet.scope.model_dump() != PUBLIC_SCOPE:
        raise LLMInvalidResponse("generation scope mismatch")
    if not 1 <= len(packet.applied_rules) <= 5:
        raise _NotApplicable("generation claim bound")
    claims, seen = [], set()
    for applied in packet.applied_rules:
        rule = registry.rules.get(applied.rule_id)
        if rule is None or applied.rule_id in seen or rule.get("rule_id") != applied.rule_id:
            raise LLMInvalidResponse("invalid approved claim identity")
        seen.add(applied.rule_id)
        digest = canonical_sha256(rule)
        scope = rule.get("applicability", {})
        if (digest != applied.rule_sha256 or digest != registry.rule_hashes.get(applied.rule_id)
                or not _approved(rule) or scope.get("basis") != "admission_year"
                or scope.get("admission_years") != [2026] or scope.get("curriculum_years") != [2026]
                or scope.get("departments") != [PUBLIC_SCOPE["department"]]
                or rule.get("answer_policy") == "record_only"):
            raise LLMInvalidResponse("invalid approved claim basis")
        for evidence in rule.get("evidence", []):
            source = registry.sources.get(evidence["source_id"])
            if source is None or not _approved(source):
                raise LLMInvalidResponse("unapproved claim source")
        if not rule.get("evidence"):
            raise LLMInvalidResponse("missing approved claim source")
        statement = rule["decision"]["statement"]
        outcome = rule["decision"]["outcome"]
        if outcome["type"] == "coverage_requirement" and outcome.get("requirement") == "major.required.course_set":
            statement += " 지정 과목은 " + ", ".join(item["label"] for item in outcome["items"]) + "이다."
        claim = {"claim_id": applied.rule_id, "rule_sha256": digest, "statement": statement}
        if (outcome["type"] == "credit_threshold" and outcome.get("comparator") == "at_least"
                and rule.get("relationship", {}).get("kind") == "base" and scope.get("conditions") == []
                and outcome.get("metric") in _SUBJECTS):
            claim.update(metric=outcome["metric"], subject=_SUBJECTS[outcome["metric"]], credits=outcome["credits"])
        claims.append(claim)
    if sum(len(claim["statement"]) for claim in claims) > 1800:
        raise _NotApplicable("generation statement bound")
    plan = {"grammar_version": GRAMMAR_VERSION, "scope": dict(PUBLIC_SCOPE), "claims": claims}
    plan["basis_sha256"] = canonical_sha256(plan)
    validate_public_plan(plan)
    return plan


def validate_public_plan(plan: dict) -> None:
    """Reject unexpected/private fields before transport or cache lookup."""
    if (not isinstance(plan, dict) or set(plan) != {"grammar_version", "scope", "claims", "basis_sha256"}
            or plan["grammar_version"] != GRAMMAR_VERSION or plan["scope"] != PUBLIC_SCOPE
            or not isinstance(plan["claims"], list) or not 1 <= len(plan["claims"]) <= 5):
        raise LLMInvalidResponse("invalid public generation plan")
    seen, chars = set(), 0
    for claim in plan["claims"]:
        if (not isinstance(claim, dict) or set(claim) not in (
                {"claim_id", "rule_sha256", "statement"},
                {"claim_id", "rule_sha256", "statement", "metric", "subject", "credits"})
                or not isinstance(claim.get("claim_id"), str)
                or re.fullmatch(r"cwnu\.cs\.2026\.[a-z0-9.-]{1,150}", claim["claim_id"]) is None
                or claim["claim_id"] in seen or not isinstance(claim.get("rule_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", claim["rule_sha256"]) is None
                or not isinstance(claim.get("statement"), str) or not claim["statement"]
                or any(ord(char) < 32 for char in claim["statement"])):
            raise LLMInvalidResponse("invalid public generation claim")
        if "metric" in claim and (claim["metric"] not in _SUBJECTS
                or claim["subject"] != _SUBJECTS[claim["metric"]]
                or type(claim["credits"]) is not int or not 0 <= claim["credits"] <= 500):
            raise LLMInvalidResponse("invalid public credit grammar")
        seen.add(claim["claim_id"])
        chars += len(claim["statement"])
    if chars > 1800 or plan["basis_sha256"] != canonical_sha256({key: value for key, value in plan.items() if key != "basis_sha256"}):
        raise LLMInvalidResponse("invalid public generation digest")


def permitted_sentences(claim: dict) -> tuple[str, ...]:
    sentences = [claim["statement"], "확인된 기준에 따르면, " + claim["statement"]]
    if "metric" in claim:
        subject, credits = claim["subject"], claim["credits"]
        particle = "은" if (ord(subject[-1]) - 0xAC00) % 28 else "는"
        sentences.extend((f"{subject}{particle} 최소 {credits}학점을 이수해야 합니다.",
                          f"{subject} 기준은 {credits}학점 이상입니다."))
    return tuple(sentences)


def output_schema(plan: dict) -> dict:
    validate_public_plan(plan)
    return {"type": "object", "additionalProperties": False,
            "required": ["basis_sha256", "introduction", "sentences"], "properties": {
                "basis_sha256": {"type": "string", "enum": [plan["basis_sha256"]]},
                "introduction": {"type": "string", "enum": list(INTRODUCTIONS)},
                "sentences": {"type": "array", "minItems": len(plan["claims"]), "maxItems": len(plan["claims"]),
                    "items": {"type": "object", "additionalProperties": False, "required": ["claim_id", "text"],
                        "properties": {"claim_id": {"type": "string", "enum": [claim["claim_id"] for claim in plan["claims"]]},
                                       "text": {"type": "string", "minLength": 1, "maxLength": 2048}}}}}}


def verify_document(plan: dict, document: dict, *, cached: bool = False) -> VerifiedGeneration:
    validate_public_plan(plan)
    if (not isinstance(document, dict) or set(document) != {"basis_sha256", "introduction", "sentences"}
            or document["basis_sha256"] != plan["basis_sha256"] or document["introduction"] not in INTRODUCTIONS
            or not isinstance(document["sentences"], list) or len(document["sentences"]) != len(plan["claims"])):
        raise LLMInvalidResponse("invalid grounded generation coverage")
    texts = []
    for sentence, claim in zip(document["sentences"], plan["claims"], strict=True):
        if (not isinstance(sentence, dict) or set(sentence) != {"claim_id", "text"}
                or sentence["claim_id"] != claim["claim_id"] or sentence["text"] not in permitted_sentences(claim)):
            raise LLMInvalidResponse("invalid grounded generation sentence")
        texts.append(sentence["text"])
    answer = "\n".join(([document["introduction"]] if document["introduction"] else []) + texts)
    return VerifiedGeneration(answer, tuple(claim["claim_id"] for claim in plan["claims"]), cached, document)


def generate_grounded(base: AcademicAnswerResponse, registry: Registry, client: LocalLLMClient | None) -> GenerationOutcome:
    try:
        plan = build_public_plan(base, registry)
        if client is None:
            return GenerationOutcome("unavailable")
        result = client.generate_grounded(plan)
        # Rebuild from the current registry and revalidate the actual text at the
        # consumer boundary, including a forged/mock client's return value.
        if build_public_plan(base, registry) != plan or not isinstance(result, VerifiedGeneration):
            raise LLMInvalidResponse("generation basis changed")
        verified = verify_document(plan, result.document, cached=result.cached)
        if (verified.answer, verified.claim_ids) != (result.answer, result.claim_ids) or type(result.cached) is not bool:
            raise LLMInvalidResponse("generation result mismatch")
        return GenerationOutcome("cached" if result.cached else "generated", result.answer, result.claim_ids)
    except _NotApplicable:
        return GenerationOutcome("not_applicable")
    except LLMBusy:
        return GenerationOutcome("busy")
    except LLMUnavailable:
        return GenerationOutcome("unavailable")
    except (LLMInvalidResponse, ValueError, TypeError, KeyError, AttributeError):
        return GenerationOutcome("rejected")
    except Exception:
        return GenerationOutcome("unavailable")
