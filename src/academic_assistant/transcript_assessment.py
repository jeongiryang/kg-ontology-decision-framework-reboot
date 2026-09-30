"""Partial, deterministic assessment. Never substitutes for official graduation review."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from .core import validate_request_safety
from .models import AcademicAnswerRequest
from .registry import RegistryUnavailable, canonical_bytes
from .transcript_models import (
    TranscriptAssessmentRequest, TranscriptAssessmentResponse, TranscriptCheck,
    TranscriptFollowupRequest, TranscriptFollowupResponse, TranscriptEvidencePacket,
)

PREFIX = "cwnu.cs.2026."
PASS_GRADES = {"A+", "A0", "B+", "B0", "C+", "C0", "D+", "D0", "P", "PASS", "S"}


def _identity(name):
    return re.sub(r"\s+", "", name)


class TranscriptAssessor:
    def __init__(self, engine):
        self.engine = engine
        self.registry = engine.registry

    def assess(self, request: TranscriptAssessmentRequest) -> TranscriptAssessmentResponse:
        if (request.admission_year, request.matched_curriculum_year) != (2026, 2026):
            return TranscriptAssessmentResponse(status="out_of_scope", conclusion="out_of_scope", answer="2026학번·2026 교육과정 성적표만 지원합니다. 다른 입학년도 성적표에 2026 기준을 적용하지 않습니다.", raw_earned_credits=0, recognized_graduation_credits=None, checks=[], issues=["지원 범위 밖"])
        if request.degree_track == "multiple_major":
            return TranscriptAssessmentResponse(status="out_of_scope", conclusion="out_of_scope", answer="복수·연계·융합전공 이수유형은 아직 지원하지 않습니다. 기본 78전공·18잔여 기준을 적용하지 않습니다.", raw_earned_credits=0, recognized_graduation_credits=None, checks=[], issues=["전공 이수유형 지원 범위 밖"])
        if self.engine.evidence_reader is not None:
            self.engine.evidence_reader.verify(self.registry)
        if self.registry.conflicts:
            return TranscriptAssessmentResponse(status="conflict", conclusion="needs_review", answer="승인된 규칙의 충돌로 계산을 보류합니다.", raw_earned_credits=0, recognized_graduation_credits=None, checks=[], issues=["학사 규칙 충돌"])
        rules = self.registry.rules
        required_rule = PREFIX + "major-required-course-set"
        items = rules[required_rule]["decision"]["outcome"]["items"]
        required_by_code = {item["item_id"]: item["label"] for item in items}
        required_by_name = {_identity(item["label"]): item["item_id"] for item in items}
        passed = [row for row in request.courses if not row.excluded and row.grade in PASS_GRADES]
        issues = []
        identities = defaultdict(list)
        completed = set()
        ambiguous_identity = False
        invalid_required = False
        unmatched_required = False
        for row in passed:
            name_code = required_by_name.get(_identity(row.course_name))
            if row.course_code and name_code and row.course_code != name_code:
                ambiguous_identity = True
            if row.course_code in required_by_code and _identity(row.course_name) != _identity(required_by_code[row.course_code]):
                ambiguous_identity = True
            code = row.course_code or name_code
            if code in required_by_code:
                expected_credits = 0 if code in {"CDA0034", "CDA0088"} else 3
                if row.category != "major_required" or row.credits != expected_credits:
                    invalid_required = True
                else:
                    completed.add(code)
            elif row.category == "major_required":
                unmatched_required = True
            # Exact approved names and codes refer to one identity even when
            # one record omits its code. Never merge or choose duplicate rows.
            identities[code or _identity(row.course_name)].append(row)
        # Repeated zero-credit counseling does not double-count any credit.
        duplicates = any(len(rows) > 1 and not all(row.credits == 0 and _identity(row.course_name) == "심층상담" for row in rows) for rows in identities.values())
        unknown = any(row.category == "unknown" for row in passed)
        if duplicates:
            issues.append("동일 과목의 중복 이수 기록이 있습니다. 재수강 전 기록을 제외할지 확인해야 합니다. 최신·최고 성적을 자동 선택하지 않습니다.")
        if ambiguous_identity:
            issues.append("과목 코드와 과목명이 서로 다릅니다. 과목 식별을 수정해야 합니다.")
        if unknown:
            issues.append("이수구분 미확인 과목이 있어 학점 배분을 확정할 수 없습니다.")
        if invalid_required:
            issues.append("승인된 필수과목의 이수구분 또는 학점이 다릅니다. 전공필수·3학점 기준과 논문·상담 0학점 기록을 직접 확인해 주세요.")
        if unmatched_required:
            issues.append("승인된 필수과목 목록과 일치하지 않는 전공필수 기록이 있습니다. 전공필수 학점 인정을 확인해야 합니다.")
        if not request.record_complete:
            issues.append("전체 이수내역 확인이 없어 미등장 과목을 미이수로 확정하지 않습니다.")
        totals = {key: sum(row.credits for row in passed if row.category == key) for key in ("foundation", "balanced", "expanded", "major_required", "major_elective", "free")}
        raw = sum(row.credits for row in passed)
        gf = rules[PREFIX + "credits.general-foundation"]["decision"]["outcome"]["credits"]
        gb = rules[PREFIX + "credits.general-balanced"]["decision"]["outcome"]["credits"]
        general = totals["foundation"] + totals["balanced"] + totals["expanded"]
        cap = rules[PREFIX + "credits.general-recognition-cap"]["decision"]["outcome"]["maximum_recognized_credits"]
        general_remaining = max(0, min(general, cap) - min(totals["foundation"], gf) - min(totals["balanced"], gb))
        major = totals["major_required"] + totals["major_elective"]
        recognized = min(general, cap) + major + totals["free"]
        blocked = duplicates or unknown or ambiguous_identity or invalid_required or unmatched_required
        track_unknown = request.degree_track == "unknown"
        if track_unknown:
            issues.append("전공 이수유형 미확인: 기본 전공78·심화33·졸업잔여18 및 총학점 판정을 보류합니다.")
        values = {
            "credits.general.foundation": totals["foundation"], "credits.general.balanced": totals["balanced"],
            "credits.general.remaining": general_remaining, "credits.general.total": general,
            "credits.major.required": totals["major_required"], "credits.major.elective": totals["major_elective"],
            "credits.major.minimum": min(totals["major_required"], 21) + min(totals["major_elective"], 24),
            "credits.major.total": major, "credits.graduation.remaining": totals["free"],
            "credits.graduation.total": recognized,
        }
        checks = []
        scope = {"admission_year": 2026, "matched_curriculum_year": 2026, "department": request.department}
        digest = hashlib.sha256(canonical_bytes({"request": request.model_dump(), "registry": self.registry.digest})).hexdigest()[:24]

        def packet(ids, key, facts, review=False):
            if review:
                return TranscriptEvidencePacket(
                    packet_id=f"transcript-{digest}-{key}", scope=scope, student_facts=facts,
                    applied_rules=[], evidence=[], status="insufficient_evidence",
                    issues=[{"kind": "review", "message": "확인된 기록 또는 학점 배분 근거가 부족합니다.", "related_ids": []}],
                )
            evidence = []
            for rule_id in ids:
                rule = rules[rule_id]
                if rule["review"]["status"] != "approved":
                    raise RegistryUnavailable()
                evidence.extend({"source_id": item["source_id"], "rule_id": rule_id, "locator": item["locator"], "claim": rule["decision"]["statement"]} for item in rule["evidence"])
            if self.engine.evidence_reader is not None:
                evidence = self.engine.evidence_reader.fetch_evidence(ids, self.registry)
            return TranscriptEvidencePacket(packet_id=f"transcript-{digest}-{key}", scope=scope, student_facts=facts,
                applied_rules=[{"rule_id": rule_id, "rule_sha256": self.registry.rule_hashes[rule_id]} for rule_id in ids],
                evidence=evidence, issues=[], status="supported")

        def packets(ids, key, facts, review=False):
            return {"evidence_packet": packet(ids, key, facts, review),
                    "policy_packet": packet(ids, key + "-policy", {}) if review else None}

        for metric, earned in values.items():
            suffix = "credits." + metric.removeprefix("credits.").replace(".", "-")
            rule_id = PREFIX + suffix
            ids = [rule_id]
            if metric == "credits.general.remaining":
                ids.extend([PREFIX + "credits.general-remaining-allocation", PREFIX + "credits.general-foundation", PREFIX + "credits.general-balanced", PREFIX + "credits.general-recognition-cap"])
            if metric == "credits.major.minimum":
                ids.extend([PREFIX + "credits.major-required", PREFIX + "credits.major-elective"])
            if metric == "credits.graduation.total":
                ids.append(PREFIX + "credits.general-recognition-cap")
            if metric == "credits.graduation.remaining":
                ids.append(PREFIX + "credits.graduation-remaining-allocation")
            required = rules[rule_id]["decision"]["outcome"]["credits"]
            review = blocked or (not request.record_complete and earned < required) or (track_unknown and metric in {"credits.major.total", "credits.graduation.remaining", "credits.graduation.total"})
            checks.append(TranscriptCheck(check_id=metric, label=rules[rule_id]["label"], result="needs_review" if review else ("met" if earned >= required else "not_met"),
                required=required, earned=None if blocked else earned, gap=None if review else max(0, required-earned),
                note="이수구분·중복 기록 확인 필요" if blocked else ("학점 기준의 부분 비교입니다. 영역·과목 요건은 별도로 확인합니다."),
                **packets(ids, metric, {} if blocked else {metric: earned}, review)))
        advanced = PREFIX + "credits.major-advanced"
        checks.append(TranscriptCheck(check_id="credits.major.advanced", label=rules[advanced]["label"], result="needs_review", required=rules[advanced]["decision"]["outcome"]["credits"],
            note="성적표의 전공선택을 심화전공으로 배분하는 과목별 근거가 아직 없습니다. 전선 초과분을 임의로 심화학점에 넣지 않습니다.", **packets([advanced], "advanced", {}, True)))
        for key, label, codes, ids in (
            ("major.required.course_set", "남은 전공필수 과목", set(required_by_code), [required_rule]),
            ("graduation.thesis", "0학점 졸업논문 이수", {required_by_name["졸업논문"]}, [PREFIX + "graduation.thesis-required", PREFIX + "graduation.thesis-completion-result", required_rule]),
            ("major.counseling", "심층상담 최소 1회 이수", {required_by_name["심층상담"]}, [PREFIX + "major-counseling-completion", required_rule]),
        ):
            missing = [required_by_code[code] for code in required_by_code if code in codes - completed]
            review = blocked or (bool(missing) and not request.record_complete)
            checks.append(TranscriptCheck(check_id=key, label=label, result="needs_review" if review else ("not_met" if missing else "met"), missing_courses=[] if review else missing,
                note="전 과목 PASS 이수 기준의 비교이며 개인 면제·대체 여부는 별도 검수입니다. 0학점 과목도 확인합니다.", **packets(ids, key, {} if blocked else {"completed_courses": len(codes & completed)}, review)))
        area_rule = PREFIX + "general-balanced-area-coverage"
        areas = {item["item_id"] for item in rules[area_rule]["decision"]["outcome"]["items"]}
        coverage = {row.balanced_area for row in passed if row.category == "balanced" and row.balanced_area}
        area_unknown = any(row.category == "balanced" and row.balanced_area is None for row in passed)
        area_review = blocked or (coverage != areas and (area_unknown or not request.record_complete))
        checks.append(TranscriptCheck(check_id="general.balanced.area_coverage", label="균형교양 4영역 이수", result="needs_review" if area_review else ("met" if coverage == areas else "not_met"), required=4, earned=len(coverage), gap=None if area_review else 4-len(coverage),
            note="성적표에 없는 영역은 추측하지 않습니다. 확인된 과목별 영역을 입력하세요.", **packets([area_rule], "areas", {} if blocked else {"balanced_areas": len(coverage)}, area_review)))
        issues.extend(["심화전공 과목별 배분, 개인 면제·대체·소급 적용 및 보류 운영요건은 별도 확인이 필요합니다.", "취득학점과 졸업인정학점은 다릅니다. 교양은 졸업학점에 최대 42학점만 반영합니다."])
        unmet = any(check.result == "not_met" for check in checks)
        return TranscriptAssessmentResponse(status="insufficient_evidence", conclusion="approved_requirements_not_met" if unmet else "needs_review",
            answer="확인된 기록에서 미충족 학점·과목을 찾았습니다. 아래 항목별 근거를 확인하세요. 최종 졸업 인증은 아닙니다." if unmet else "확인된 이수 기록을 비교했습니다. 아직 확인할 요건이 있어 졸업 가능을 확정하지 않습니다.",
            raw_earned_credits=raw, recognized_graduation_credits=None if blocked or track_unknown else recognized, checks=checks, issues=issues)

    def followup(self, request: TranscriptFollowupRequest) -> TranscriptFollowupResponse:
        safe_request = AcademicAnswerRequest(question=request.question, admission_year=request.transcript.admission_year, matched_curriculum_year=request.transcript.matched_curriculum_year, department=request.transcript.department)
        validate_request_safety(safe_request)
        result = self.assess(request.transcript)
        if result.status in {"out_of_scope", "conflict"}:
            return TranscriptFollowupResponse(status=result.status, answer=result.answer, selected_checks=[])
        question = re.sub(r"\s+", "", request.question)
        catalog = {
            "남은필수과목은뭐야?": ["major.required.course_set"],
            "남은필수과목": ["major.required.course_set"],
            "몇학점남았어?": ["credits.graduation.total"],
            "졸업학점부족분": ["credits.graduation.total"],
            "교양학점부족분": [key for key in values_keys() if key.startswith("credits.general.")],
            "전공학점부족분": [key for key in values_keys() if key.startswith("credits.major.")],
            "졸업논문이수했어?": ["graduation.thesis"],
            "심층상담이수했어?": ["major.counseling"],
        }
        keys = catalog.get(question)
        if keys is None:
            return TranscriptFollowupResponse(status="insufficient_evidence", answer="개인 성적표 질문은 화면의 정해진 질문 버튼을 사용하세요. 면제·대체·소급·운영요건은 추정하지 않습니다.", selected_checks=[])
        selected = [check for check in result.checks if check.check_id in keys]
        supported = all(check.evidence_packet.status == "supported" for check in selected)
        parts = []
        for check in selected:
            if check.result == "needs_review":
                parts.append(f"{check.label}: 확인 필요. {check.note}")
            elif check.gap is not None:
                parts.append(f"{check.label}: {check.gap}학점 부족.")
            elif check.missing_courses:
                parts.append(f"{check.label}: " + ", ".join(check.missing_courses))
            else:
                parts.append(f"{check.label}: 확인된 이수 기록에서 충족.")
        return TranscriptFollowupResponse(status="supported" if supported else "insufficient_evidence", answer=" ".join(parts) + " 최종 졸업 인증은 아닙니다.", selected_checks=selected)


def values_keys():
    return ("credits.general.foundation", "credits.general.balanced", "credits.general.remaining", "credits.general.total", "credits.major.required", "credits.major.elective", "credits.major.minimum", "credits.major.total", "credits.major.advanced")
