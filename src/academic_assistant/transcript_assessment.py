"""Partial, deterministic assessment. Never substitutes for official graduation review."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from .core import _question_exceeds_scope, normalize_text, validate_request_safety
from .models import AcademicAnswerRequest
from .registry import RegistryUnavailable, canonical_bytes
from .conversation import current_transcript_question, followup_kind, friendly_transcript_query, transcript_presentation
from .transcript_models import (
    TranscriptAssessmentRequest, TranscriptAssessmentResponse, TranscriptCheck,
    TranscriptFollowupRequest, TranscriptFollowupResponse, TranscriptEvidencePacket,
    TranscriptCreditSummary, TranscriptVerificationItem,
)

PREFIX = "cwnu.cs.2026."
PASS_GRADES = {"A+", "A0", "B+", "B0", "C+", "C0", "D+", "D0", "P", "PASS", "S"}


def _identity(name):
    # The approved original catalogue uses 데이타; the reviewed rule list uses
    # 데이터. This explicit spelling equivalence does not merge distinct codes.
    return re.sub(r"\s+", "", name).replace("데이타", "데이터")


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
        active = [row for row in request.courses if not row.excluded]
        passed = [row for row in active if row.grade in PASS_GRADES]
        issues = []
        identities = defaultdict(list)
        completed = set()
        ambiguous_rows = []
        invalid_required_rows = []
        unmatched_required_rows = []
        held_rows = set()
        verification = []
        recognition_checks = [*values_keys(), "credits.graduation.remaining", "credits.graduation.total",
                              "major.required.course_set", "graduation.thesis", "major.counseling",
                              "general.balanced.area_coverage"]

        def verify(key, kind, rows, check_ids, message, action, severity="blocking", holds_credits=False):
            row_ids = [row.row_id for row in rows]
            verification.append(TranscriptVerificationItem(
                item_id="verify-" + key, kind=kind, severity=severity, row_ids=row_ids,
                check_ids=check_ids, message=message, action=action,
            ))
            if holds_credits:
                held_rows.update(row_ids)

        for row in active:
            name_code = required_by_name.get(_identity(row.course_name))
            if row.course_code and name_code and row.course_code != name_code:
                ambiguous_rows.append(row)
            if row.course_code in required_by_code and _identity(row.course_name) != _identity(required_by_code[row.course_code]):
                if row not in ambiguous_rows:
                    ambiguous_rows.append(row)
            code = row.course_code or name_code
            if row.grade in PASS_GRADES and code in required_by_code:
                expected_credits = 0 if code in {"CDA0034", "CDA0088"} else 3
                if row.category != "major_required" or row.credits != expected_credits:
                    invalid_required_rows.append(row)
                elif not row.review_flags and row not in ambiguous_rows:
                    completed.add(code)
            elif row.grade in PASS_GRADES and row.category == "major_required":
                unmatched_required_rows.append(row)
            # Exact approved names and codes refer to one identity even when
            # one record omits its code. Never merge or choose duplicate rows.
            identities[code or _identity(row.course_name)].append(row)
        # Repeated zero-credit counseling does not double-count any credit.
        duplicate_groups = [rows for rows in identities.values() if len(rows) > 1
                            and any(row.grade in PASS_GRADES for row in rows)
                            and not all(row.credits == 0 and _identity(row.course_name) == "심층상담" for row in rows)]
        unknown_rows = [row for row in passed if row.category == "unknown"]
        if duplicate_groups:
            issues.append("동일 과목의 중복 이수 기록이 있습니다. 재수강 전 기록을 제외할지 확인해야 합니다. 최신·최고 성적을 자동 선택하지 않습니다.")
            for index, rows in enumerate(duplicate_groups, 1):
                verify(f"duplicate-{index}", "duplicate_or_retake", rows, recognition_checks,
                       "동일 과목 또는 재수강으로 보이는 기록이 함께 있습니다.",
                       "공식 재수강 처리 내역을 확인한 뒤 제외할 기록을 직접 표시하고 다시 비교하세요. 최신·최고 성적을 자동 선택하지 않습니다.", holds_credits=True)
        if ambiguous_rows:
            issues.append("과목 코드와 과목명이 서로 다릅니다. 과목 식별을 수정해야 합니다.")
            verify("identity", "course_identity", ambiguous_rows, recognition_checks,
                   "승인된 필수과목의 코드와 과목명이 일치하지 않습니다.",
                   "공식 성적표와 과목코드를 확인하여 해당 행의 코드·과목명을 수정하세요.", holds_credits=True)
        if unknown_rows:
            issues.append("이수구분 미확인 과목이 있어 학점 배분을 확정할 수 없습니다.")
            verify("category", "category", unknown_rows, recognition_checks,
                   "PASS 과목의 이수구분이 미확인입니다.",
                   "공식 이수구분을 확인하여 해당 행의 구분을 입력한 뒤 다시 비교하세요.", holds_credits=True)
        if invalid_required_rows:
            issues.append("승인된 필수과목의 이수구분 또는 학점이 다릅니다. 전공필수·3학점 기준과 논문·상담 0학점 기록을 직접 확인해 주세요.")
            verify("required-record", "course_identity", invalid_required_rows, recognition_checks,
                   "필수과목 기록의 이수구분 또는 학점이 승인된 과목 목록과 다릅니다.",
                   "공식 성적표의 학점·이수구분과 개인 인정 내역을 확인하세요. 입력값을 임의로 보정하지 않습니다.", holds_credits=True)
        if unmatched_required_rows:
            issues.append("승인된 필수과목 목록과 일치하지 않는 전공필수 기록이 있습니다. 전공필수 학점 인정을 확인해야 합니다.")
            verify("unmatched-required", "course_identity", unmatched_required_rows, recognition_checks,
                   "전공필수로 입력된 과목이 승인된 필수과목 목록과 일치하지 않습니다.",
                   "공식 과목코드·과목명과 대체 지정 또는 개인 인정 내역을 확인하세요.", holds_credits=True)
        for flag, kind, message, action in (
            ("retake", "duplicate_or_retake", "재수강 처리 확인이 필요한 기록입니다.", "공식 재수강 삭제·인정 내역을 확인하고 제외할 행을 직접 표시하세요."),
            ("equivalence", "equivalence", "동일·대체과목 지정 확인이 필요한 기록입니다.", "공식 동일·대체 지정과 적용 시점을 확인하세요. 과목명 유사성만으로 인정하지 않습니다."),
            ("retroactivity", "retroactivity", "소급 적용 확인이 필요한 기록입니다.", "개인 이수내역에 적용되는 공식 소급 인정 근거와 시점을 확인하세요."),
            ("recognition_unverified", "course_identity", "개인 과목 인정이 아직 확인되지 않은 기록입니다.", "공식 인정·면제 내역을 확인한 뒤 해당 행의 확인 표시를 갱신하세요."),
        ):
            rows = [row for row in active if flag in row.review_flags]
            if rows:
                issues.append(message)
                verify("flag-" + flag, kind, rows, recognition_checks, message, action, holds_credits=True)
        if not request.record_complete:
            issues.append("전체 이수내역 확인이 없어 미등장 과목을 미이수로 확정하지 않습니다.")
            verify("record-completeness", "record_completeness", [], recognition_checks,
                   "전체 이수내역인지 확인되지 않아 미등장 과목과 부족분 판정을 보류합니다.",
                   "모든 학기의 이수내역과 0학점 과목을 확인한 뒤 전체 이수내역 확인을 선택하세요.")
        totals = {key: sum(row.credits for row in passed if row.category == key) for key in ("foundation", "balanced", "expanded", "major_required", "major_elective", "free")}
        raw = sum(row.credits for row in passed)
        gf = rules[PREFIX + "credits.general-foundation"]["decision"]["outcome"]["credits"]
        gb = rules[PREFIX + "credits.general-balanced"]["decision"]["outcome"]["credits"]
        general = totals["foundation"] + totals["balanced"] + totals["expanded"]
        cap = rules[PREFIX + "credits.general-recognition-cap"]["decision"]["outcome"]["maximum_recognized_credits"]
        general_remaining = max(0, min(general, cap) - min(totals["foundation"], gf) - min(totals["balanced"], gb))
        major = totals["major_required"] + totals["major_elective"]
        recognized = min(general, cap) + major + totals["free"]
        blocked = bool(held_rows)
        track_unknown = request.degree_track == "unknown"
        if track_unknown:
            issues.append("전공 이수유형 미확인: 기본 전공78·심화33·졸업잔여18 및 총학점 판정을 보류합니다.")
            verify("degree-track", "degree_track", [],
                   ["credits.major.total", "credits.major.advanced", "credits.graduation.remaining", "credits.graduation.total"],
                   "전공 이수유형이 미확인입니다.", "공식 이수유형을 확인하여 전공 이수유형을 선택하세요. 다전공 유형은 현재 지원 범위 밖입니다.")
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
        verify("advanced-allocation", "advanced_allocation", [row for row in passed if row.category == "major_elective"],
               ["credits.major.advanced"], "심화전공 과목별 배분 근거가 아직 승인되지 않아 비교를 보류합니다.",
               "학과의 공식 과목별 심화 인정·배분 내역을 확인하세요. 전선 초과분을 자동 배분하지 않습니다.")
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
        if area_unknown:
            verify("balanced-area", "balanced_area", [row for row in passed if row.category == "balanced" and row.balanced_area is None],
                   ["general.balanced.area_coverage"], "균형교양 과목의 영역이 미확인입니다.",
                   "공식 과목별 영역을 확인하여 해당 행의 균형교양 영역을 입력하세요.",
                   severity="blocking" if area_review else "advisory")
        checks.append(TranscriptCheck(check_id="general.balanced.area_coverage", label="균형교양 4영역 이수", result="needs_review" if area_review else ("met" if coverage == areas else "not_met"), required=4, earned=len(coverage), gap=None if area_review else 4-len(coverage),
            note="성적표에 없는 영역은 추측하지 않습니다. 확인된 과목별 영역을 입력하세요.", **packets([area_rule], "areas", {} if blocked else {"balanced_areas": len(coverage)}, area_review)))
        issues.extend(["심화전공 과목별 배분, 개인 면제·대체·소급 적용 및 보류 운영요건은 별도 확인이 필요합니다.", "취득학점과 졸업인정학점은 다릅니다. 교양은 졸업학점에 최대 42학점만 반영합니다."])
        unmet = any(check.result == "not_met" for check in checks)
        return TranscriptAssessmentResponse(status="insufficient_evidence", conclusion="approved_requirements_not_met" if unmet else "needs_review",
            answer="확인된 기록에서 미충족 학점·과목을 찾았습니다. 아래 항목별 근거를 확인하세요. 최종 졸업 인증은 아닙니다." if unmet else "확인된 이수 기록을 비교했습니다. 아직 확인할 요건이 있어 졸업 가능을 확정하지 않습니다.",
            raw_earned_credits=raw, recognized_graduation_credits=None if blocked or track_unknown else recognized, checks=checks, issues=issues,
            verification_items=verification, credit_summary=TranscriptCreditSummary(
                input_pass_credits=raw, conditional_graduation_credits=None if blocked or track_unknown else recognized,
                unresolved_pass_credits=sum(row.credits for row in passed if row.row_id in held_rows),
                recognition_status="needs_review" if blocked or track_unknown else "partial_comparison"))

    def followup(self, request: TranscriptFollowupRequest) -> TranscriptFollowupResponse:
        if request.response_style == "friendly":
            return self._friendly_followup(request)
        return self._legacy_followup(request)

    def _friendly_followup(self, request: TranscriptFollowupRequest) -> TranscriptFollowupResponse:
        current_assessment = None

        def assess_current():
            nonlocal current_assessment
            if current_assessment is None:
                current_assessment = self.assess(request.transcript)
            return current_assessment

        def legacy(question):
            return self._legacy_followup(request.model_copy(update={"question": question}),
                                         _assessment=assess_current)

        current_question = current_transcript_question(request.question)
        original = legacy(current_question)
        question = normalize_text(current_question)
        compact = question.replace(" ", "")
        guards = ("protected_aliases", "exception_aliases", "negation_aliases", "disjunction_aliases")
        blocked = any(alias in compact for group in guards for alias in self.registry.intents.get(group, [])) or any(
            token in compact for token in ("면제", "자동", "가능", "인정돼", "인정되", "졸업할"))
        anchor, used = None, False
        response = original
        kind = followup_kind(current_question)

        def refuse(message):
            return TranscriptFollowupResponse(status="insufficient_evidence", answer=message, selected_checks=[])

        if original.status not in {"out_of_scope", "conflict"} and not blocked:
            resolved = friendly_transcript_query(current_question)
            if kind is not None:
                if request.previous_question is None:
                    response = refuse("어떤 성적표 비교 항목을 말씀하시는지 먼저 적어 주세요. 남은 학점을 총학점으로 추정하지 않아요.")
                else:
                    previous_question = current_transcript_question(request.previous_question)
                    prior_query = friendly_transcript_query(previous_question)
                    prior = legacy(previous_question)
                    if prior.status not in {"out_of_scope", "conflict"}:
                        prior = legacy(prior_query)
                    if prior.status in {"out_of_scope", "conflict"}:
                        response = prior
                    elif prior.status != "supported" or not prior.selected_checks:
                        response = refuse("이전 비교 항목을 현재 성적표와 근거로 확인할 수 없어요. 확인할 항목을 직접 질문해 주세요.")
                    elif kind != "explain" and len(prior.selected_checks) != 1:
                        response = refuse("이전 답변에는 여러 비교 항목이 있어요. 기초교양·교양 총학점·전공필수처럼 대상을 하나 적어 주세요.")
                    else:
                        check = prior.selected_checks[0]
                        compatible = (kind == "explain" or (kind in {"gap", "credits"} and check.check_id.startswith("credits."))
                                      or (kind in {"courses", "course_reference"} and check.check_id == "major.required.course_set")
                                      or (kind == "count" and check.check_id == "major.counseling"))
                        if not compatible:
                            response = refuse("이전 항목과 질문의 학점·과목·횟수 단위가 달라요. 비교할 항목을 직접 적어 주세요.")
                        else:
                            response, resolved, used = prior, prior_query, True
            else:
                response = legacy(resolved)
            if response.status == "supported" and response.selected_checks:
                anchor = resolved
        return TranscriptFollowupResponse.model_validate({
            **response.model_dump(), "conversational_answer": transcript_presentation(response),
            "context_question": anchor, "context_used": used})

    def _legacy_followup(self, request: TranscriptFollowupRequest, *, _assessment=None) -> TranscriptFollowupResponse:
        safe_request = AcademicAnswerRequest(question=request.question, admission_year=request.transcript.admission_year, matched_curriculum_year=request.transcript.matched_curriculum_year, department=request.transcript.department)
        validate_request_safety(safe_request)
        question = normalize_text(request.question)
        if _question_exceeds_scope(question):
            return TranscriptFollowupResponse(status="out_of_scope",
                answer="질문에 지정된 학번·교육과정·학과가 2026학번·2026 교육과정·컴퓨터공학과 지원 범위와 다릅니다.", selected_checks=[])
        compact = question.replace(" ", "")
        guards = ("protected_aliases", "exception_aliases", "negation_aliases", "disjunction_aliases")
        if any(alias in compact for group in guards for alias in self.registry.intents.get(group, [])) or any(
            token in compact for token in ("면제", "자동", "가능", "인정돼", "인정되", "졸업할")):
            return TranscriptFollowupResponse(status="insufficient_evidence",
                answer="운영요건·예외·면제 또는 부정·선택 표현이 함께 있는 질문은 부분만 골라 판정할 수 없습니다. 하나의 이수 비교 항목이나 확인할 기록을 질문해 주세요.", selected_checks=[])
        result = _assessment() if _assessment is not None else self.assess(request.transcript)
        if result.status in {"out_of_scope", "conflict"}:
            return TranscriptFollowupResponse(status=result.status, answer=result.answer, selected_checks=[])
        keys, verification_kinds = _followup_targets(request.question)
        if verification_kinds is not None:
            items = [item for item in result.verification_items if not verification_kinds or item.kind in verification_kinds]
            focus = list(dict.fromkeys(check_id for item in items for check_id in item.check_ids))
            return TranscriptFollowupResponse(status="insufficient_evidence",
                answer=" ".join(f"{item.message} {item.action}" for item in items) if items else "입력된 기록에서 해당 확인 항목은 발견되지 않았습니다. 공식 인정 여부를 확정하는 답변은 아닙니다.",
                selected_checks=[check for check in result.checks if check.check_id in focus],
                focus_check_ids=focus, verification_items=items)
        if keys is None:
            return TranscriptFollowupResponse(status="insufficient_evidence", answer="부족한 학점, 남은 필수과목, 논문·상담 이수 또는 확인할 항목을 질문해 주세요. 예: '교양 몇 학점이 부족해?', '남은 전공필수 과목 알려줘', '확인이 필요한 항목은?'. 개인 면제·대체·소급 인정과 최종 졸업 여부는 확정하지 않습니다.", selected_checks=[])
        selected = [check for check in result.checks if check.check_id in keys]
        supported = bool(selected) and all(check.evidence_packet.status == "supported" for check in selected)
        parts = []
        for check in selected:
            if check.result == "needs_review":
                parts.append(f"{check.label}: 확인 필요. {check.note}")
            elif check.gap is not None:
                unit = "영역" if check.check_id == "general.balanced.area_coverage" else "학점"
                parts.append(f"{check.label}: {check.gap}{unit} 부족.")
            elif check.missing_courses:
                parts.append(f"{check.label}: " + ", ".join(check.missing_courses))
            else:
                parts.append(f"{check.label}: 확인된 이수 기록에서 충족.")
        return TranscriptFollowupResponse(status="supported" if supported else "insufficient_evidence", answer=" ".join(parts) + " 최종 졸업 인증은 아닙니다.", selected_checks=selected,
            focus_check_ids=[check.check_id for check in selected],
            verification_items=[item for item in result.verification_items if set(item.check_ids) & set(keys)])


def values_keys():
    return ("credits.general.foundation", "credits.general.balanced", "credits.general.remaining", "credits.general.total", "credits.major.required", "credits.major.elective", "credits.major.minimum", "credits.major.total", "credits.major.advanced")


def _followup_targets(question):
    """Consume one complete question; never discard unmatched topics or clauses."""
    compact = normalize_text(question).replace(" ", "")
    scope = r"(?:(?:(?:2026|26)(?:학번|학년도|교육과정)(?:컴퓨터공학과)?|컴퓨터공학과(?:(?:2026|26)(?:학번|학년도|교육과정))?)(?:의|에서)?)?"
    personal = r"(?:내가|제가|나는|저는|나의|저의|내|제|입력한기록에서|확인된기록에서)?"
    prefix = scope + personal
    ask = r"(?:알려줘|알려주세요|알려줄래|알려줄래요|보여줘|보여주세요|확인해줘|확인해주세요|알고싶어|알고싶어요)"
    suffix = rf"(?:은|는|이|가|을|를)?(?:뭐야|뭔가요|무엇인가요|뭐가있어|뭐가있나요|{ask})?"

    def matches(pattern):
        return re.fullmatch(prefix + pattern, compact) is not None

    # Verification grammars only select existing review items. They do not
    # determine retake deletion, equivalence, retroactivity or personal approval.
    review = rf"(?:확인(?:이필요한(?:항목|기록|과목)|할(?:항목|기록|과목)|해야할(?:항목|기록|과목))?|검토(?:할)?(?:항목|기록|과목)|체크리스트){suffix}"
    for topic, kind in (
        (r"(?:재수강|중복(?:과목|기록)?)", "duplicate_or_retake"),
        (r"(?:동일대체과목|동일대체|동일과목|대체과목|대체)", "equivalence"),
        (r"소급(?:적용)?", "retroactivity"),
        (r"이수구분", "category"),
        (r"(?:과목코드|과목명|과목인정|개인인정)", "course_identity"),
        (r"심화(?:전공)?(?:학점)?(?:배분)?", "advanced_allocation"),
        (r"(?:균형교양|균형)(?:영역)?|영역", "balanced_area"),
        (r"(?:전공)?이수유형", "degree_track"),
        (r"전체(?:이수내역|이수기록|이수|기록)", "record_completeness"),
    ):
        if matches(rf"(?:{topic})(?:의|은|는|이|가|을|를)?{review}"):
            return None, {kind}
    if matches(rf"(?:확인(?:이필요한|할|해야할|해야하는)?(?:항목|사항|기록)|검토(?:할|해야할|가필요한)?(?:항목|사항)|검수(?:할)?항목|체크리스트){suffix}"):
        return None, set()

    if matches(rf"(?:남은|남아있는|아직안들은|안들은|듣지않은|미이수인|미이수)(?:전공)?필수과목{suffix}") or matches(
        rf"(?:전공)?필수과목(?:은|이)?(?:뭐가남았어|뭐가남았나요|무엇이남았나요|남은게뭐야|모두이수했어|전부들었나요)"):
        return ["major.required.course_set"], None
    completion = rf"(?:이수(?:완료)?(?:했어|했나요|했는지{ask}|{ask})?|완료(?:했어|했나요|됐어|됐나요)|충족(?:했어|했나요)|남았어|남았나요)"
    if matches(rf"졸업논문(?:은|이|을)?{completion}"):
        return ["graduation.thesis"], None
    if matches(rf"(?:심층)?상담(?:은|이|을)?{completion}"):
        return ["major.counseling"], None
    if matches(rf"(?:균형교양|균형)(?:4)?영역(?:은|이|을)?(?:얼마나부족해|몇영역(?:이)?부족해|얼마나남았어|부족분|부족한영역{ask}|이수했어|이수했나요)"):
        return ["general.balanced.area_coverage"], None

    gap_end = r"(?:부족해|부족한가요|남았어|남았나요|남아|남아요|필요해|필요한가요|들어야해|들어야하나요|모자라|모자라나요)"
    gap = rf"(?:몇학점(?:이|을)?(?:더)?{gap_end}|얼마나(?:더)?{gap_end}|부족분(?:{ask})?|부족한학점{ask}|남은학점{ask})(?:요)?"
    for topic, keys in (
        (r"기초교양", ["credits.general.foundation"]),
        (r"균형교양", ["credits.general.balanced"]),
        (r"교양잔여", ["credits.general.remaining"]),
        (r"교양", [key for key in values_keys() if key.startswith("credits.general.")]),
        (r"전공필수", ["credits.major.required"]),
        (r"(?:전공선택|전선)", ["credits.major.elective"]),
        (r"(?:최소전공|전공최소)", ["credits.major.minimum"]),
        (r"심화(?:전공)?", ["credits.major.advanced"]),
        (r"전공", [key for key in values_keys() if key.startswith("credits.major.")]),
        (r"(?:자유선택(?:잔여)?|졸업잔여|잔여)", ["credits.graduation.remaining"]),
        (r"(?:졸업(?:까지)?|총|전체)", ["credits.graduation.total"]),
    ):
        if matches(rf"(?:{topic})(?:학점)?(?:은|는|이|을)?{gap}"):
            return keys, None
    if matches(rf"(?:몇학점(?:이)?{gap_end}|남은학점{ask})"):
        return ["credits.graduation.total"], None
    return None, None
