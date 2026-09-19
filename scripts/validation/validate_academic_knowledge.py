#!/usr/bin/env python3
"""Validate academic source, rule, relationship, and human-review artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


TA_SOURCE_ID = "cwnu.curriculum.2026.ta-validation-response"
TA_SOURCE_SHA256 = "c5a7839675bd02679018788190961613d68b3126ba411b060710987162cb336d"
TA_RULE_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "cwnu.cs.2026.cohort.department-transfer-original-admission-year": {
        "statement": "전과 완료 연도가 아니라 학생의 최초 입학연도에 해당하는 교육과정을 적용한다.",
        "answer_policy": "policy_statement",
        "outcome": {
            "type": "cohort_assignment_policy",
            "cohort_kind": "department_transfer",
            "assignment_basis": "original_admission_year",
            "completion_year_ignored": True,
        },
        "ta_locator": "PDF p.1, section 1: 적용 대상 필기와 사용자 정정",
    },
    "cwnu.cs.2026.graduation.thesis-completion-result": {
        "statement": "졸업논문은 0학점이어도 반드시 이수해야 하며 미이수하면 Fail이다.",
        "answer_policy": "policy_statement",
        "outcome": {
            "type": "completion_requirement",
            "requirement": "graduation.thesis.required",
            "credit_value": 0,
            "required": True,
            "result_if_incomplete": "fail",
        },
        "ta_locator": "PDF p.3, section 4: 졸업논문 필기와 사용자 정정",
    },
    "cwnu.cs.2026.graduation.thesis-linked-program-exemption": {
        "statement": "학·석사 연계과정생은 졸업논문 이수 면제가 가능하지만 자동 면제로 판정하지 않는다.",
        "answer_policy": "record_only",
        "outcome": {
            "type": "exception_eligibility",
            "target_requirement": "graduation.thesis.required",
            "eligible_group": "student.program.bachelors_masters_linked",
            "eligibility": "may_be_exempt",
            "automatic": False,
        },
        "ta_locator": "PDF p.3, section 4: 오른쪽 필기와 사용자 정정",
    },
    "cwnu.cs.2026.course-counting.retake": {
        "statement": "재수강 시 기이수 과목은 삭제되므로 학점을 중복 계산하지 않는다.",
        "answer_policy": "policy_statement",
        "outcome": {"type": "course_counting_policy", "scenario": "retake", "action": "delete_prior_completion", "double_counted": False, "individual_determination": "not_required"},
        "ta_locator": "PDF p.3, section 5: 재수강 필기와 사용자 정정",
    },
    "cwnu.cs.2026.course-counting.identical-course": {
        "statement": "동일교과목은 중복 수강신청되지 않으므로 중복 계산되지 않는다.",
        "answer_policy": "policy_statement",
        "outcome": {"type": "course_counting_policy", "scenario": "identical_course_registration", "action": "registration_blocked", "double_counted": False, "individual_determination": "not_required"},
        "ta_locator": "PDF p.3, section 5: 동일교과목 필기와 사용자 정정",
    },
    "cwnu.cs.2026.course-counting.post-completion-equivalence": {
        "statement": "이수 후 대체 또는 동일 과목으로 지정된 경우에는 별개의 과목으로 학점 수를 계산하되, 소급 적용되는 경우 결과가 달라질 수 있어 개인 판정은 보류한다.",
        "answer_policy": "policy_statement",
        "outcome": {"type": "course_counting_policy", "scenario": "post_completion_equivalent_or_substitute", "action": "count_as_separate_courses", "double_counted": False, "individual_determination": "required_if_retroactive"},
        "ta_locator": "PDF p.3, section 5: 동일·대체 지정 필기와 사용자 정정",
    },
}


def _load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path.as_posix()}: cannot load JSON: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path.as_posix()}: JSON root must be an object")
        return None
    return value


def _validator(project: Path, filename: str) -> Draft202012Validator:
    schema = json.loads((project / "contracts" / filename).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_instance(
    validator: Draft202012Validator, instance: dict[str, Any], path: Path, errors: list[str]
) -> None:
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{path.as_posix()}:{location}: {error.message}")


def validate_knowledge(project: Path) -> list[str]:
    project = project.resolve()
    errors: list[str] = []
    source_dir = project / "knowledge" / "sources"
    rule_dir = project / "knowledge" / "rules"
    review_dir = project / "reviews" / "academic"

    source_files = sorted(source_dir.glob("*.json")) if source_dir.is_dir() else []
    rule_files = sorted(rule_dir.glob("*.json")) if rule_dir.is_dir() else []
    review_files = sorted(review_dir.glob("*.json")) if review_dir.is_dir() else []
    if not source_files:
        errors.append("knowledge/sources: at least one SourceEntry JSON is required")
    if not rule_files:
        errors.append("knowledge/rules: at least one RuleFact JSON is required")
    if not review_files:
        errors.append("reviews/academic: at least one AcademicReviewPacket JSON is required")

    source_validator = _validator(project, "source-entry.schema.json")
    rule_validator = _validator(project, "rule-fact.schema.json")
    review_validator = _validator(project, "academic-review-packet.schema.json")

    sources: dict[str, tuple[dict[str, Any], Path]] = {}
    rules: dict[str, tuple[dict[str, Any], Path]] = {}
    reviews: list[tuple[dict[str, Any], Path]] = []

    for path in source_files:
        value = _load_json(path, errors)
        if value is None:
            continue
        _validate_instance(source_validator, value, path.relative_to(project), errors)
        source_id = value.get("source_id")
        if isinstance(source_id, str):
            if source_id in sources:
                errors.append(f"{path.relative_to(project)}: duplicate source_id {source_id}")
            sources[source_id] = (value, path)

    for path in rule_files:
        value = _load_json(path, errors)
        if value is None:
            continue
        _validate_instance(rule_validator, value, path.relative_to(project), errors)
        rule_id = value.get("rule_id")
        if isinstance(rule_id, str):
            if rule_id in rules:
                errors.append(f"{path.relative_to(project)}: duplicate rule_id {rule_id}")
            rules[rule_id] = (value, path)

    for path in review_files:
        value = _load_json(path, errors)
        if value is None:
            continue
        _validate_instance(review_validator, value, path.relative_to(project), errors)
        reviews.append((value, path))

    for rule_id, (rule, path) in rules.items():
        relative = path.relative_to(project)
        for evidence in rule.get("evidence", []):
            source_id = evidence.get("source_id") if isinstance(evidence, dict) else None
            if source_id not in sources:
                errors.append(f"{relative}: rule {rule_id} references unknown source {source_id!r}")
        relationship = rule.get("relationship", {})
        for target in relationship.get("target_rule_ids", []) if isinstance(relationship, dict) else []:
            if target not in rules:
                errors.append(f"{relative}: rule {rule_id} references unknown target rule {target}")

        review = rule.get("review", {})
        if isinstance(review, dict) and review.get("status") == "approved":
            authoritative_evidence = False
            higher_than_validation_aid = False
            for evidence in rule.get("evidence", []):
                if not isinstance(evidence, dict):
                    continue
                source = sources.get(evidence.get("source_id"))
                if source is not None and source[0].get("review", {}).get("status") == "approved":
                    if source[0].get("authority") != "validation_aid" and evidence.get("evidence_type") in {
                        "explicit_text",
                        "table_structure",
                        "department_confirmation",
                    }:
                        authoritative_evidence = True
                    if source[0].get("authority") != "validation_aid":
                        higher_than_validation_aid = True
            if not authoritative_evidence:
                errors.append(
                    f"{relative}: approved rule {rule_id} requires approved authoritative evidence"
                )
            if any(
                isinstance(item, dict) and item.get("source_id") == TA_SOURCE_ID
                for item in rule.get("evidence", [])
            ) and not higher_than_validation_aid:
                errors.append(
                    f"{relative}: TA validation aid cannot be the only higher-authority basis for {rule_id}"
                )

        outcome = rule.get("decision", {}).get("outcome", {})
        if isinstance(outcome, dict) and outcome.get("type") in {
            "credit_recognition_cap", "coverage_requirement", "allocation_policy",
            "completion_requirement", "exception_eligibility", "cohort_assignment_policy",
            "course_counting_policy", "recommendation",
        } and rule.get("answer_policy") not in {"policy_statement", "calculation", "record_only"}:
            errors.append(f"{relative}: extended typed outcome requires answer_policy")
        if outcome.get("type") == "exception_eligibility" and rule.get("answer_policy") != "record_only":
            errors.append(f"{relative}: individual exception eligibility must be record_only")
        if outcome.get("individual_determination") == "required_if_retroactive" and rule.get("answer_policy") != "policy_statement":
            errors.append(f"{relative}: confirmed general course-counting policy must remain answerable")

        expected = TA_RULE_EXPECTATIONS.get(rule_id)
        if expected is not None:
            if rule.get("decision", {}).get("statement") != expected["statement"]:
                errors.append(f"{relative}: approved TA statement does not match exact confirmation")
            if rule.get("answer_policy") != expected["answer_policy"]:
                errors.append(f"{relative}: approved TA answer_policy changed")
            if outcome != expected["outcome"]:
                errors.append(f"{relative}: approved TA typed outcome changed")
            if not any(
                item.get("source_id") == TA_SOURCE_ID
                and item.get("locator") == expected["ta_locator"]
                for item in rule.get("evidence", []) if isinstance(item, dict)
            ):
                errors.append(f"{relative}: approved TA page locator changed")
            if rule.get("review", {}).get("status") != "approved":
                errors.append(f"{relative}: confirmed TA rule must remain approved")

    ta_source = sources.get(TA_SOURCE_ID)
    if ta_source is not None:
        source = ta_source[0]
        if source.get("sha256") != TA_SOURCE_SHA256 or source.get("authority") != "validation_aid":
            errors.append("knowledge/sources: TA validation source identity or authority changed")
        if source.get("review", {}).get("status") != "approved":
            errors.append("knowledge/sources: TA validation source must retain approved human review")

    thesis_required = {
        rule.get("decision", {}).get("outcome", {}).get("requirement")
        for rule_id, (rule, _) in rules.items()
        if rule.get("decision", {}).get("outcome", {}).get("type") == "boolean_requirement"
        and str(rule.get("decision", {}).get("outcome", {}).get("requirement", "")).startswith(
            "graduation.thesis"
        )
        and rule.get("decision", {}).get("outcome", {}).get("required") is True
    }
    thesis_substitution = {
        rule.get("decision", {}).get("outcome", {}).get("target_requirement")
        for rule_id, (rule, _) in rules.items()
        if rule.get("decision", {}).get("outcome", {}).get("type") == "substitution_policy"
        and str(
            rule.get("decision", {}).get("outcome", {}).get("target_requirement", "")
        ).startswith("graduation.thesis")
    }
    if not thesis_required:
        errors.append("knowledge/rules: graduation thesis requirement RuleFact is required")
    if not thesis_substitution:
        errors.append("knowledge/rules: graduation thesis substitution-policy RuleFact is required")
    if thesis_required and thesis_substitution and thesis_required.isdisjoint(thesis_substitution):
        errors.append(
            "knowledge/rules: graduation thesis substitution policy must target the requirement fact"
        )

    expected_subjects = {("source", source_id) for source_id in sources} | {
        ("rule", rule_id) for rule_id in rules
    }
    observed_subjects: set[tuple[str, str]] = set()
    review_subjects: dict[tuple[str, str], tuple[dict[str, Any], Path]] = {}
    for review, path in reviews:
        subjects = review.get("subjects", [])
        subject_statuses: list[str] = []
        for subject in subjects if isinstance(subjects, list) else []:
            if not isinstance(subject, dict):
                continue
            key = (subject.get("subject_type"), subject.get("subject_id"))
            if key in observed_subjects:
                errors.append(f"{path.relative_to(project)}: duplicate review subject {key}")
            if isinstance(key[0], str) and isinstance(key[1], str):
                observed_subjects.add((key[0], key[1]))
                review_subjects[(key[0], key[1])] = (subject, path)
            subject_statuses.append(subject.get("status"))
        if review.get("overall_status") == "approved" and any(
            status != "approved" for status in subject_statuses
        ):
            errors.append(
                f"{path.relative_to(project)}: approved review packet contains non-approved subjects"
            )
        if subject_statuses and all(status == "approved" for status in subject_statuses):
            if review.get("overall_status") != "approved":
                errors.append(
                    f"{path.relative_to(project)}: all review subjects are approved but "
                    "overall_status is not approved"
                )
    for missing in sorted(expected_subjects - observed_subjects):
        errors.append(f"reviews/academic: missing review subject {missing[0]}:{missing[1]}")
    for extra in sorted(observed_subjects - expected_subjects):
        errors.append(f"reviews/academic: unknown review subject {extra[0]}:{extra[1]}")

    objects = {
        **{("source", identifier): value for identifier, (value, _) in sources.items()},
        **{("rule", identifier): value for identifier, (value, _) in rules.items()},
    }
    for key, value in objects.items():
        queued = review_subjects.get(key)
        if queued is None:
            continue
        subject, path = queued
        object_review = value.get("review", {})
        object_status = object_review.get("status")
        queue_status = subject.get("status")
        if object_status in {"approved", "rejected"}:
            if queue_status != object_status:
                errors.append(
                    f"{path.relative_to(project)}: review subject {key} is {queue_status!r} "
                    f"but object review is {object_status!r}"
                )
            for queue_field, object_field in (
                ("reviewer_id", "reviewer_id"),
                ("reviewed_at", "reviewed_at"),
                ("comment", "rationale"),
            ):
                if subject.get(queue_field) != object_review.get(object_field):
                    errors.append(
                        f"{path.relative_to(project)}: review subject {key} field "
                        f"{queue_field} does not match object {object_field}"
                    )
        elif queue_status not in {"pending", "needs_revision"}:
            errors.append(
                f"{path.relative_to(project)}: review subject {key} is {queue_status!r} "
                f"but object review is {object_status!r}"
            )

    return sorted(set(errors))


def canonical_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def validate_evidence_packet(project: Path, packet: dict[str, Any]) -> list[str]:
    """Validate a packet against the current approved source and rule registry."""
    project = project.resolve()
    errors: list[str] = list(validate_knowledge(project))
    _validate_instance(
        _validator(project, "evidence-packet.schema.json"), packet, Path("<packet>"), errors
    )
    if errors:
        return sorted(set(errors))

    sources: dict[str, dict[str, Any]] = {}
    rules: dict[str, dict[str, Any]] = {}
    for path in sorted((project / "knowledge" / "sources").glob("*.json")):
        value = _load_json(path, errors)
        if value is not None and isinstance(value.get("source_id"), str):
            sources[value["source_id"]] = value
    for path in sorted((project / "knowledge" / "rules").glob("*.json")):
        value = _load_json(path, errors)
        if value is not None and isinstance(value.get("rule_id"), str):
            rules[value["rule_id"]] = value

    research_ids: set[str] = set()
    research_claims: set[str] = set()
    for path in sorted((project / "reviews" / "academic" / "research").glob("*.json")):
        value = _load_json(path, errors)
        if value is None:
            continue
        if isinstance(value.get("research_id"), str):
            research_ids.add(value["research_id"])
        for claim in value.get("claims", []):
            if not isinstance(claim, dict):
                continue
            if isinstance(claim.get("claim_id"), str):
                research_ids.add(claim["claim_id"])
            if isinstance(claim.get("statement"), str):
                research_claims.add(claim["statement"])

    scope = packet["scope"]
    applied_ids: set[str] = set()
    for applied in packet["applied_rules"]:
        rule_id = applied["rule_id"]
        if rule_id in research_ids:
            errors.append(f"EvidencePacket: unverified research item cannot be applied: {rule_id}")
        if rule_id in applied_ids:
            errors.append(f"EvidencePacket: duplicate applied rule_id {rule_id}")
            continue
        applied_ids.add(rule_id)
        rule = rules.get(rule_id)
        if rule is None:
            errors.append(f"EvidencePacket: unknown applied rule {rule_id}")
            continue
        if applied["rule_sha256"] != canonical_sha256(rule):
            errors.append(f"EvidencePacket: canonical hash mismatch for rule {rule_id}")
        review = rule.get("review", {})
        if not (
            review.get("status") == "approved"
            and review.get("mode") == "human"
            and review.get("scope") == "full"
        ):
            errors.append(f"EvidencePacket: rule {rule_id} is not human/full/approved")
        applicability = rule.get("applicability", {})
        if scope["admission_year"] not in applicability.get("admission_years", []):
            errors.append(f"EvidencePacket: admission year is outside rule {rule_id} scope")
        if scope["matched_curriculum_year"] not in applicability.get("curriculum_years", []):
            errors.append(f"EvidencePacket: curriculum year is outside rule {rule_id} scope")
        if scope["department"] not in applicability.get("departments", []):
            errors.append(f"EvidencePacket: department is outside rule {rule_id} scope")

    cited_rule_ids: set[str] = set()
    for evidence in packet["evidence"]:
        rule_id = evidence["rule_id"]
        if (
            rule_id in research_ids
            or evidence["source_id"] in research_ids
            or evidence["claim"] in research_claims
        ):
            errors.append("EvidencePacket: unverified research cannot be used as answer evidence")
        if rule_id not in applied_ids:
            errors.append(f"EvidencePacket: evidence references unapplied rule {rule_id}")
            continue
        rule = rules.get(rule_id)
        if rule is None:
            continue
        matching = [
            item
            for item in rule.get("evidence", [])
            if item.get("source_id") == evidence["source_id"]
            and item.get("locator") == evidence["locator"]
        ]
        if not matching:
            errors.append(
                f"EvidencePacket: evidence for rule {rule_id} does not match a registered source and locator"
            )
            continue
        cited_rule_ids.add(rule_id)
        if evidence["claim"] != rule.get("decision", {}).get("statement"):
            errors.append(
                f"EvidencePacket: claim does not match the registered decision for rule {rule_id}"
            )
        source = sources.get(evidence["source_id"])
        if source is None:
            errors.append(f"EvidencePacket: unknown evidence source {evidence['source_id']}")
            continue
        source_review = source.get("review", {})
        if not (
            source_review.get("status") == "approved"
            and source_review.get("mode") == "human"
            and source_review.get("scope") == "full"
        ):
            errors.append(
                f"EvidencePacket: source {evidence['source_id']} is not human/full/approved"
            )

    for rule_id in sorted(applied_ids - cited_rule_ids):
        errors.append(f"EvidencePacket: applied rule {rule_id} has no registered citation")

    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    errors = validate_knowledge(args.project)
    if errors:
        for error in errors:
            print(f"academic-knowledge: error: {error}", file=sys.stderr)
        return 1
    print("academic-knowledge: sources, rules, relationships, and review queue passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
