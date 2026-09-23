from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from itertools import product
from typing import Any

from .models import AcademicAnswerRequest, AcademicAnswerResponse
from .registry import Registry, canonical_bytes

_PUNCTUATION = re.compile(r"[^0-9a-zA-Z가-힣]+")
_ACADEMIC_WORDS = ("학점", "졸업", "교양", "전공", "논문", "학사", "이수", "교육과정", "수강", "학기", "재수강", "휴학", "복학", "편입", "전과", "재입학", "캡스톤", "pccp", "공모전", "졸업작품")
_GAP_WORDS = ("부족", "모자", "남았", "남은", "더 들어", "더 이수")
_UNSAFE_QUESTION = re.compile(r"(?:이름|성명|학번|학생번호|주민등록|성적표|raw\s*transcript|student\s*(?:name|id|number)|[0-9]{6,12}|01[016789][ -]?[0-9]{3,4}[ -]?[0-9]{4}|[\w.+-]+@[\w.-]+\.[a-z]{2,})", re.IGNORECASE)
_PERSONAL_TOKEN = re.compile(
    r"^(?:전|난|"
    r"저(?:는|도|의|라면|로서는|에게|에게도|에게는|에게만|한테|한테도|한테는|한테만)?|"
    r"제(?:가|게|게도|게는|게만|의)?|"
    r"저희들(?:은|이|도|의|에게|에게도|에게는|에게만|한테|한테도|한테는|한테만)?|"
    r"저희(?:는|가|도|의|에게|에게도|에게는|에게만|한테|한테도|한테는|한테만)?|"
    r"나(?:는|도|의|에게|에게도|에게는|에게만|한테|한테도|한테는|한테만)?|"
    r"내(?:가|게|게도|게는|게만|의)?|"
    r"우리들(?:은|이|도|의|에게|에게도|에게는|에게만|한테|한테도|한테는|한테만)?|"
    r"우리(?:는|가|도|의|에게|에게도|에게는|에게만|한테|한테도|한테는|한테만)?|"
    r"본인(?:은|이|도|의|에게|에게도|에게는|에게만|한테|한테도|한테는|한테만)?)$"
)
APPROVED_METRICS = frozenset({
    "credits.general.balanced", "credits.general.foundation", "credits.general.remaining", "credits.general.total",
    "credits.graduation.remaining", "credits.graduation.total", "credits.major.advanced", "credits.major.elective",
    "credits.major.minimum", "credits.major.required", "credits.major.total",
})
SANITIZED_DEPARTMENT = "지원범위외"
_ALIAS_SUFFIXES = frozenset({
    "은", "는", "이", "가", "을", "를", "의", "도", "만", "에", "에서", "으로", "로", "와", "과",
    "인가", "인가요", "이라면", "이라도", "요건", "요건은", "요건이", "기준", "기준은", "기준이",
    "학점", "학점은", "학점이",
})


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return " ".join(_PUNCTUATION.sub(" ", value).split())


def _compact(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def _alias_spans(alias: str, normalized: str) -> list[tuple[int, int]]:
    tokens = normalized.split()
    offsets: list[int] = []
    offset = 0
    for token in tokens:
        offsets.append(offset)
        offset += len(token)
    spans: list[tuple[int, int]] = []
    for start in range(len(tokens)):
        joined = ""
        for token in tokens[start:]:
            joined += token
            if joined == alias:
                spans.append((offsets[start], offsets[start] + len(alias)))
                break
            if alias.startswith(joined):
                continue
            if joined.startswith(alias):
                if joined[len(alias):] in _ALIAS_SUFFIXES:
                    spans.append((offsets[start], offsets[start] + len(alias)))
                break
            break
    return spans


def _alias_matches(alias: str, normalized: str) -> bool:
    return bool(_alias_spans(alias, normalized))


def _has_final_consonant(text: str) -> bool:
    if not text:
        return False
    code = ord(text[-1])
    return 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0


def validate_request_safety(payload: AcademicAnswerRequest) -> None:
    validate_public_text_safety(payload.question)
    if set(payload.earned_credits) - APPROVED_METRICS:
        raise ValueError("unknown earned-credit metric")


def validate_public_text_safety(value: str) -> None:
    if _UNSAFE_QUESTION.search(value) or any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise ValueError("unsafe or identifying question content")


class AnswerEngine:
    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry or Registry.load()

    def validate_request(self, payload: AcademicAnswerRequest) -> None:
        validate_request_safety(payload)
        if self.registry.allowed_metrics != APPROVED_METRICS:
            raise ValueError("registry metric profile mismatch")

    def answer(self, request: AcademicAnswerRequest) -> AcademicAnswerResponse:
        self.validate_request(request)
        normalized_request = {
            "question": normalize_text(request.question),
            "admission_year": request.admission_year,
            "matched_curriculum_year": request.matched_curriculum_year,
            "department": normalize_text(request.department),
            "earned_credits": dict(sorted(request.earned_credits.items())),
        }
        packet_id = "academic-" + hashlib.sha256(canonical_bytes({"request": normalized_request, "registry": self.registry.digest})).hexdigest()[:32]
        scope = {"admission_year": request.admission_year, "matched_curriculum_year": request.matched_curriculum_year, "department": request.department}

        if (request.admission_year, request.matched_curriculum_year, normalize_text(request.department)) != (2026, 2026, "컴퓨터공학과"):
            safe_scope = {**scope, "department": SANITIZED_DEPARTMENT}
            return self._unsupported(packet_id, safe_scope, "out_of_scope", "지원 범위는 2026학번·2026 교육과정·컴퓨터공학과입니다.", "scope")

        question = normalize_text(request.question)
        compact = question.replace(" ", "")
        config = self.registry.intents
        if any(alias in compact for alias in config["protected_aliases"]):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "해당 운영 관행은 공식 근거가 확인되지 않아 답변할 수 없습니다.", "review")
        if any(alias in compact for alias in config["exception_aliases"]):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "해당 예외 적용에는 별도의 승인된 근거가 필요합니다.", "missing")
        if self._is_individual_determination(question, compact):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "개인별 이수·면제·소급 적용 결과를 판정하려면 공식 학적 확인이 필요합니다.", "missing")
        if any(alias in compact for alias in config["broad_graduation_aliases"]):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "포괄적인 졸업 인증 여부를 판정할 승인 근거가 충분하지 않습니다.", "missing")
        specific_aliases = [alias for entry in config["intents"] if entry["kind"] == "specific" for alias in entry["aliases"]]
        if any(alias in compact for alias in config["negation_aliases"]) or any(f"비{alias}" in compact for alias in specific_aliases):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "부정 또는 대비 표현이 포함되어 적용할 규칙을 확정할 수 없습니다.", "missing")
        preliminary = self._select_intents(compact, question)
        if preliminary and any(alias in compact for alias in config["disjunction_aliases"]):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "선택형 표현이 포함되어 적용할 규칙을 확정할 수 없습니다.", "missing")
        if len(preliminary) > 1 and not self._has_explicit_conjunction(preliminary, question):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "여러 규칙을 연결하는 승인된 접속 표현이 없어 적용 대상을 확정할 수 없습니다.", "missing")
        selected_metrics = {
            self.registry.rules[rule_id]["decision"]["outcome"]["metric"]
            for intent in preliminary
            for rule_id in intent["rule_ids"]
            if self.registry.rules[rule_id]["decision"]["outcome"]["type"] == "credit_threshold"
        }
        if set(request.earned_credits) - selected_metrics:
            raise ValueError("unrelated earned-credit metric")
        selected = preliminary
        if not selected:
            if any(word in question for word in _ACADEMIC_WORDS):
                return self._unsupported(packet_id, scope, "insufficient_evidence", "질문에 답할 승인된 학사 근거가 없습니다.", "missing")
            return self._unsupported(packet_id, scope, "out_of_scope", "지원하는 학사 질문 범위가 아닙니다.", "scope")

        rules = []
        intent_ids = []
        for intent in selected:
            intent_ids.append(intent["intent_id"])
            rules.extend(intent["rule_ids"])
        rule_ids = list(dict.fromkeys(rules))
        conflict_ids: set[str] = set()
        for rule_id in rule_ids:
            outcome = self.registry.rules[rule_id]["decision"]["outcome"]
            identity = outcome.get("metric") or outcome.get("requirement") or outcome.get("target_requirement")
            key = f"{outcome['type']}:{identity}" if identity else None
            if key in self.registry.conflicts:
                conflict_ids.update(self.registry.conflicts[key])
        if conflict_ids:
            return self._unsupported(packet_id, scope, "conflict", "승인된 규칙 사이에 충돌이 있어 판정을 보류합니다.", "conflict", sorted(conflict_ids), intent_ids)

        credit_rules = [self.registry.rules[rule_id] for rule_id in rule_ids if self.registry.rules[rule_id]["decision"]["outcome"]["type"] == "credit_threshold"]
        gap_requested = any(word in question for word in _GAP_WORDS) or ("몇 학점" in question and "기준" not in question)
        if gap_requested:
            selected_metrics = [rule["decision"]["outcome"]["metric"] for rule in credit_rules]
            if not selected_metrics or any(metric not in request.earned_credits for metric in selected_metrics):
                return self._unsupported(packet_id, scope, "insufficient_evidence", "부족 학점을 계산하려면 선택된 항목의 현재 이수학점이 모두 필요합니다.", "missing", intent_ids=intent_ids)

        calculations = []
        for rule in credit_rules:
            outcome = rule["decision"]["outcome"]
            metric = outcome["metric"]
            if metric in request.earned_credits:
                earned = request.earned_credits[metric]
                calculations.append({"metric": metric, "required": outcome["credits"], "earned": earned, "gap": max(0, outcome["credits"] - earned)})

        applied = [{"rule_id": rule_id, "rule_sha256": self.registry.rule_hashes[rule_id]} for rule_id in rule_ids]
        evidence = []
        statements = []
        for rule_id in rule_ids:
            rule = self.registry.rules[rule_id]
            statements.append(rule["decision"]["statement"])
            for item in rule["evidence"]:
                evidence.append({"source_id": item["source_id"], "rule_id": rule_id, "locator": item["locator"], "claim": rule["decision"]["statement"]})
        answer_text = " ".join(statements)
        if calculations:
            answer_text += " " + " ".join(f"{c['metric']}은(는) {c['earned']}학점 이수하여 {c['gap']}학점이 부족합니다." for c in calculations)
        packet = {"schema_version": "2.0.0", "packet_id": packet_id, "scope": scope, "student_facts": dict(request.earned_credits), "applied_rules": applied, "evidence": evidence, "issues": [], "status": "supported"}
        return AcademicAnswerResponse(packet_id=packet_id, status="supported", answer=answer_text, intent_ids=intent_ids, calculations=calculations, evidence_packet=packet)

    def _select_intents(self, compact: str, normalized: str) -> list[dict[str, Any]]:
        intents = self.registry.intents["intents"]
        matched = [
            (entry, self._entry_alias_spans(entry, normalized))
            for entry in intents
            if entry["kind"] == "specific"
        ]
        matched = [(entry, spans) for entry, spans in matched if spans]
        specifics = [
            entry
            for entry, spans in matched
            if any(
                not any(
                    other_span[0] <= span[0]
                    and span[1] <= other_span[1]
                    and other_span != span
                    for other, other_spans in matched
                    if other is not entry
                    for other_span in other_spans
                )
                for span in spans
            )
        ]
        substitution = {entry["intent_id"] for entry in specifics if entry["intent_id"] == "graduation.thesis.substitution"}
        linked_exemption = {entry["intent_id"] for entry in specifics if entry["intent_id"] == "graduation.thesis.linked-program-exemption"}
        thesis_conjunction = "졸업논문과" in compact or "논문과" in compact or any(token in {"및", "그리고"} for token in normalized.split())
        if substitution and not thesis_conjunction:
            specifics = [entry for entry in specifics if entry["intent_id"] != "graduation.thesis.required"]
        if linked_exemption and not thesis_conjunction:
            specifics = [entry for entry in specifics if entry["intent_id"] != "graduation.thesis.required"]
        if specifics:
            return specifics
        return [entry for entry in intents if entry["kind"] == "bundle" and self._entry_alias_spans(entry, normalized)]

    def _is_individual_determination(self, normalized: str, compact: str) -> bool:
        aliases = self.registry.intents["individual_determination_aliases"]
        if any(_alias_matches(alias, normalized) for alias in aliases):
            return True
        tokens = normalized.split()
        marker_indexes = [
            index
            for index, token in enumerate(tokens)
            if _PERSONAL_TOKEN.fullmatch(token[:-1] if token.endswith("요") else token)
        ]
        if not marker_indexes:
            return False
        token_offsets: list[int] = []
        offset = 0
        for token in tokens:
            token_offsets.append(offset)
            offset += len(token)
        marker_offsets = [token_offsets[index] for index in marker_indexes]
        window = "".join(tokens)
        linked_exemption_shorthand = (
            "연계과정" in window
            and "학석사연계과정" not in window
            and "연계과정생" not in window
            and ("논문" in window or "면제" in window)
        )
        if linked_exemption_shorthand:
            return True
        linked_exemption_context = (
            ("학석사연계과정" in window or "연계과정생" in window)
            and "면제" in window
        )
        course_identity_context = "동일교과목" in window or "동일과목" in window
        retroactive_context = "소급" in window

        def has_scoped_policy_noun(topic_pattern: str, later_result_pattern: str) -> bool:
            match = re.search(topic_pattern + r"정책", window)
            if not match:
                return False
            if any(marker_offset >= match.end() for marker_offset in marker_offsets):
                return False
            tail = window[match.end():]
            for result_match in re.finditer(later_result_pattern, tail):
                prefix = tail[:result_match.start()]
                if not any(prefix.endswith(cue) for cue in ("누가", "누구", "누구에게", "어떻게")):
                    return False
            return bool(
                re.match(r"(?:입니다|인지)", tail)
                or re.match(r"을(?:알|알려|설명|확인)", tail)
                or re.match(r"에대해(?:알|알려|설명|확인)", tail)
                or (re.match(r"에대한", tail) and any(cue in tail for cue in ("설명", "범위", "방법")))
                or (re.match(r"의", tail) and any(cue in tail for cue in ("설명", "범위", "방법")))
                or (re.match(r"[이은]", tail) and any(cue in tail for cue in ("누구", "어떻게", "범위", "방법")))
                or (re.match(r"(?:에따르면|상)", tail) and any(cue in tail for cue in ("누가", "누구", "어떻게", "범위", "방법")))
                or (re.match(r"에따른", tail) and any(cue in tail for cue in ("계산", "범위", "방법")))
            )

        linked_policy_object = linked_exemption_context and has_scoped_policy_noun(
            r"면제(?:(?:혜택)?적용|대상)?(?:일반)?",
            r"(?:대상(?:인가|인지)|해당|면제인가|가능|자격|적용되)",
        )
        course_policy_object = course_identity_context and has_scoped_policy_noun(
            r"(?:동일교과목|동일과목)(?:의)?(?:(?:일반)?계산)?(?:일반)?",
            r"(?:동일교과목|동일과목)(?:인가|인지|에해당)",
        )
        retroactive_policy_object = retroactive_context and has_scoped_policy_noun(
            r"소급(?:적용)?(?:일반)?",
            r"소급(?:대상|적용되|여부|해당)",
        )
        if linked_exemption_context and not linked_policy_object:
            return True
        if course_identity_context and not course_policy_object:
            return True
        if retroactive_context and not retroactive_policy_object:
            return True
        return False

    def _entry_alias_spans(self, entry: dict[str, Any], normalized: str) -> list[tuple[int, int]]:
        contextual = self.registry.intents["contextual_aliases"]
        spans: list[tuple[int, int]] = []
        for alias in entry["aliases"]:
            cues = contextual.get(alias)
            if cues and not any(_alias_matches(cue, normalized) for cue in cues):
                continue
            spans.extend(_alias_spans(alias, normalized))
        return sorted(set(spans))

    def _has_explicit_conjunction(self, entries: list[dict[str, Any]], normalized: str) -> bool:
        config = self.registry.intents
        compact = normalized.replace(" ", "")
        if any(marker in compact for marker in config["comparison_aliases"]):
            return False
        options = [self._entry_alias_spans(entry, normalized) for entry in entries]
        if any(not spans for spans in options):
            return False
        word_markers = set(config["conjunction_aliases"]) - {"와", "과"}
        if normalized.split()[-1] in word_markers:
            return False
        for chosen in product(*options):
            ordered = sorted(chosen)
            if len(set(ordered)) != len(entries) or any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
                continue
            if compact[:ordered[0][0]] in config["conjunction_aliases"] or compact[ordered[-1][1]:] in config["conjunction_aliases"]:
                continue
            valid = True
            for left, right in zip(ordered, ordered[1:]):
                connector = compact[left[1]:right[0]]
                if connector in word_markers:
                    continue
                expected_particle = "과" if _has_final_consonant(compact[left[0]:left[1]]) else "와"
                if connector == expected_particle and expected_particle in config["conjunction_aliases"]:
                    continue
                valid = False
                break
            if valid:
                return True
        return False

    @staticmethod
    def _unsupported(packet_id: str, scope: dict[str, Any], status: str, message: str, kind: str, related: list[str] | None = None, intent_ids: list[str] | None = None) -> AcademicAnswerResponse:
        issue = {"kind": kind, "message": message}
        if related:
            issue["related_ids"] = related
        packet = {"schema_version": "2.0.0", "packet_id": packet_id, "scope": scope, "student_facts": {}, "applied_rules": [], "evidence": [], "issues": [issue], "status": status}
        return AcademicAnswerResponse(packet_id=packet_id, status=status, answer=message, intent_ids=intent_ids or [], calculations=[], evidence_packet=packet)


def answer(request: AcademicAnswerRequest, registry: Registry | None = None) -> AcademicAnswerResponse:
    return AnswerEngine(registry).answer(request)


def canonical_response_json(response: AcademicAnswerResponse) -> str:
    return json.dumps(response.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
