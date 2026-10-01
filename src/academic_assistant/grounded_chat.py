"""Fail-closed chat facade around the deterministic academic answer engine."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Literal

from pydantic import Field

from .core import AnswerEngine, SANITIZED_DEPARTMENT, normalize_text, validate_public_text_safety
from .llm import ALLOWED_INTENT_SIGNALS, IntentSuggester, LLMInvalidResponse, LLMUnavailable, LocalLLMClient
from .models import AcademicAnswerRequest, AcademicAnswerResponse, AcademicChatRequest, ClarificationChoice
from .registry import canonical_bytes


_NO_MATCH_ANSWER = "질문에 답할 승인된 학사 근거가 없습니다."
_SAFE_ROUTING_QUESTION = re.compile(r"[가-힣0-9 .,?!·\-]+\Z")
_INSTRUCTION_MARKERS = (
    "무시", "프롬프트", "지시", "명령", "시스템", "관리자", "개발자", "역할",
    "근거없이", "인용없이", "답해", "출력해", "비밀", "우회", "탈옥",
)
_UNVERIFIED_OPERATION_TERMS = (
    "topcit", "탑싯", "코딩테스트", "코딩시험", "코딩평가", "코테",
    "pccp", "캡스톤", "졸업작품", "공모전", "총장상", "대회수상", "외부대회",
)
_MOBILE_NUMBER = re.compile(r"(?<![0-9])01[016789](?:[ .-]?[0-9]){7,8}(?![0-9])")
_KOREAN_NAME = r"(?P<name>(?:김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권|황|안|송|전|홍|유|고|문|양|손|배|백|허|남|심|노|하|곽|성|차|주|우|구|민|진|지|엄|채|원|천|방|공|현|함|변|염|여|추|도|소|석|선|설|마|길|연|위|표|명|기|반|왕|금|옥|육|인|맹|제|모|탁|국|어|은|편|용)[가-힣]{2})"
_KOREAN_NAME_WITH_PARTICLE = re.compile(
    r"(?<![가-힣])" + _KOREAN_NAME + r"(?:에게|한테|의|은|는|이|가|을|를|도|에|씨|님)(?![가-힣])"
)
_KOREAN_BARE_NAME_TOKEN = re.compile(r"(?<![가-힣])" + _KOREAN_NAME + r"(?![가-힣])")
_ACADEMIC_NAME_LOOKALIKES = frozenset({
    "공모전", "장학금", "원전공", "한과목", "한학기", "한학점", "문제없", "우리들", "연결하", "기이수",
})
_BARE_NAME_GRAMMATICAL_ENDINGS = ("은", "는", "이", "가", "을", "를", "의", "도", "에", "게", "요", "어", "된", "상", "없", "하", "들")
_ACADEMIC_SIGNAL_TERMS = {
    "counseling": ("상담",),
    "course": ("수강", "과목", "교과"),
    "credits": ("학점",),
    "curriculum": ("교육과정",),
    "general": ("교양",),
    "graduation": ("졸업",),
    "leave": ("휴학", "복학"),
    "major": ("전공",),
    "retake": ("재수강",),
    "term": ("학기",),
    "thesis": ("논문",),
    "transfer": ("전과", "편입"),
}
assert set(_ACADEMIC_SIGNAL_TERMS) == ALLOWED_INTENT_SIGNALS


class GroundedChatResponse(AcademicAnswerResponse):
    suggested_question: str | None = None
    llm_status: Literal["disabled", "skipped", "suggested", "unavailable", "rejected"]
    clarification_choices: list[ClarificationChoice] = Field(default_factory=list, max_length=3)
    context_used: bool = False


_FOLLOWUP_PREFIX = r"(?:그럼|그러면|그건|그것은|그기준은)?"
_FOLLOWUP_PATTERNS = {
    "gap": re.compile(_FOLLOWUP_PREFIX + r"(?:몇학점(?:이)?(?:남았어|남았나요|부족해|부족한가요|더필요해요|더필요한가요)|얼마나(?:남았어|남았나요|부족해|부족한가요))"),
    "credits": re.compile(_FOLLOWUP_PREFIX + r"(?:몇학점(?:인가요|이야|필요해요|필요한가요)?|학점기준(?:은)?몇학점(?:인가요|이야)?)"),
    "courses": re.compile(_FOLLOWUP_PREFIX + r"몇과목(?:인가요|이야|들어야하나요)?"),
    "count": re.compile(_FOLLOWUP_PREFIX + r"몇(?:번|회)(?:인가요|이야|해야하나요)?"),
    "explain": re.compile(_FOLLOWUP_PREFIX + r"(?:다시설명해줘|다시설명해주세요|다시알려줘|무슨뜻인가요)"),
}
_CONTEXT_OUTCOME_TYPES = frozenset({"credit_threshold", "coverage_requirement", "completion_requirement", "boolean_requirement"})
_CHOICE_LABELS = {
    "credits.graduation.total": "졸업 총학점 기준", "credits.general.total": "교양 총학점 기준",
    "credits.major.total": "전공 총학점 기준", "credits.major.required": "전공필수 학점 기준",
    "credits.major.elective": "전공선택 학점 기준", "credits.general.foundation": "기초교양 학점 기준",
    "credits.general.balanced": "균형교양 학점 기준", "credits.general.remaining": "교양 잔여 학점 기준",
    "credits.major.advanced": "심화전공 학점 기준", "credits.major.minimum": "최소전공 학점 기준",
    "credits.graduation.remaining": "졸업 잔여 학점 기준", "major.required-course-set": "전공필수 과목 목록",
    "major.counseling-completion": "심층상담 이수 기준", "graduation.thesis.required": "졸업논문 이수 기준",
    "graduation.thesis.completion-result": "0학점 졸업논문 이수 기준",
}


def _safe_for_model(question: str) -> bool:
    normalized = unicodedata.normalize("NFKC", question).strip()
    compact = normalized.replace(" ", "")
    return (
        3 <= len(normalized) <= 200
        and _SAFE_ROUTING_QUESTION.fullmatch(normalized) is not None
        and _MOBILE_NUMBER.search(normalized) is None
        and not _contains_identifiable_name(normalized)
        and not any(marker in compact for marker in _INSTRUCTION_MARKERS)
    )


def _contains_identifiable_name(normalized: str) -> bool:
    for match in _KOREAN_NAME_WITH_PARTICLE.finditer(normalized):
        candidate = match.group("name")
        if candidate not in _ACADEMIC_NAME_LOOKALIKES and not candidate.endswith(("생", "과")):
            return True
    for match in _KOREAN_BARE_NAME_TOKEN.finditer(normalized):
        candidate = match.group("name")
        if candidate in _ACADEMIC_NAME_LOOKALIKES or candidate.endswith(("생", "과", *_BARE_NAME_GRAMMATICAL_ENDINGS)):
            continue
        return True
    return False


def _model_signal(question: str) -> str | None:
    if not _safe_for_model(question):
        return None
    compact = "".join(char for char in unicodedata.normalize("NFKC", question).lower() if char.isalnum())
    selected = sorted(
        code for code, terms in _ACADEMIC_SIGNAL_TERMS.items()
        if any(term in compact for term in terms)
    )
    return "|".join(selected) if selected else None


class GroundedChatEngine:
    """Replays one prior question through trusted, current rules and facts.

    Context resolves only whitelisted, unit-compatible follow-ups. A model is
    invoked solely when the trusted engine found no intent, and its output
    never changes status, answer, calculations, applied rules, or citations.
    """

    def __init__(self, engine: AnswerEngine | None = None, llm: IntentSuggester | None = None) -> None:
        self.engine = engine or AnswerEngine()
        self._configuration_error = False
        if llm is not None:
            self.llm = llm
        else:
            try:
                self.llm = LocalLLMClient.from_env()
            except ValueError:
                self.llm = None
                self._configuration_error = True

    @property
    def llm_configured(self) -> bool:
        return self.llm is not None and not self._configuration_error

    def chat(self, request: AcademicChatRequest | AcademicAnswerRequest) -> GroundedChatResponse:
        previous = getattr(request, "previous_question", None)
        if previous is not None:
            validate_public_text_safety(previous)
        compact = normalize_text(request.question).replace(" ", "")
        followup = next((kind for kind, pattern in _FOLLOWUP_PATTERNS.items() if pattern.fullmatch(compact)), None)
        context_used = False
        choices: list[dict[str, str]] = []
        if followup is not None:
            base, context_used, choices = self._followup(request, previous, followup)
        else:
            base = self.engine.answer(request)
        status: Literal["disabled", "skipped", "suggested", "unavailable", "rejected"] = "skipped"
        suggestion: str | None = None
        signal = _model_signal(request.question)
        if signal is not None and self._eligible(base, request.question):
            if self._configuration_error:
                status = "unavailable"
            elif self.llm is None:
                status = "disabled"
            else:
                catalog = {
                    entry["intent_id"]: entry["aliases"][0]
                    for entry in self.engine.registry.intents["intents"]
                }
                try:
                    intent_id = self.llm.suggest_intent(signal, catalog)
                except LLMUnavailable:
                    status = "unavailable"
                except (LLMInvalidResponse, ValueError, TypeError):
                    status = "rejected"
                except Exception:
                    # Optional inference can never take down the trusted answer.
                    status = "unavailable"
                else:
                    if intent_id is None:
                        status = "skipped"
                    elif isinstance(intent_id, str) and intent_id in catalog:
                        suggestion = f"혹시 ‘{catalog[intent_id]}’에 관해 질문하신 건가요?"
                        status = "suggested"
                    else:
                        status = "rejected"
        return GroundedChatResponse.model_validate({
            **base.model_dump(mode="python"),
            "suggested_question": suggestion,
            "llm_status": status,
            "clarification_choices": choices,
            "context_used": context_used,
        })

    def _choices(self, intent_ids: list[str], *, gap: bool = False) -> list[dict[str, str]]:
        entries = {entry["intent_id"]: entry for entry in self.engine.registry.intents["intents"]}
        result = []
        for intent_id in dict.fromkeys(intent_ids):
            if intent_id not in _CHOICE_LABELS or intent_id not in entries:
                continue
            canonical = entries[intent_id]["aliases"][0]
            result.append({"label": _CHOICE_LABELS[intent_id], "question": canonical + (" 부족학점" if gap else " 기준")})
            if len(result) == 3:
                break
        return result

    def _refuse_followup(
        self,
        request: AcademicChatRequest | AcademicAnswerRequest,
        previous: str | None,
        message: str,
        *,
        status: Literal["insufficient_evidence", "conflict", "out_of_scope"] = "insufficient_evidence",
    ) -> AcademicAnswerResponse:
        scope = {"admission_year": request.admission_year, "matched_curriculum_year": request.matched_curriculum_year,
                 "department": SANITIZED_DEPARTMENT if status == "out_of_scope" else request.department}
        identifier = "academic-" + hashlib.sha256(canonical_bytes({
            "question": normalize_text(request.question), "previous_question": normalize_text(previous or ""),
            "scope": scope, "earned_credits": request.earned_credits, "registry": self.engine.registry.digest,
        })).hexdigest()[:32]
        return AnswerEngine._unsupported(identifier, scope, status, message,
                                         "scope" if status == "out_of_scope" else "conflict" if status == "conflict" else "missing")

    def _followup(
        self,
        request: AcademicChatRequest | AcademicAnswerRequest,
        previous: str | None,
        kind: str,
    ) -> tuple[AcademicAnswerResponse, bool, list[dict[str, str]]]:
        self.engine.validate_request(request)
        if (request.admission_year, request.matched_curriculum_year, normalize_text(request.department)) != (2026, 2026, "컴퓨터공학과"):
            return self.engine.answer(request), False, []
        defaults = ["credits.graduation.total", "credits.general.total", "credits.major.total"]
        if not previous:
            choices = self._choices(defaults, gap=kind == "gap") if kind in {"gap", "credits"} else []
            return self._refuse_followup(request, previous, "어떤 요건을 묻는지 먼저 선택하거나 과목·영역을 질문에 적어 주세요."), False, choices
        replay = AcademicAnswerRequest(question=previous, admission_year=request.admission_year,
            matched_curriculum_year=request.matched_curriculum_year, department=request.department,
            earned_credits=dict(request.earned_credits))
        try:
            prior = self.engine.answer(replay)
        except ValueError:
            return self._refuse_followup(request, previous, "이전 질문과 현재 이수학점 항목이 맞지 않아 후속 질문을 판정할 수 없습니다."), False, []
        if prior.status != "supported":
            status = prior.status if prior.status in {"out_of_scope", "conflict"} else "insufficient_evidence"
            return self._refuse_followup(request, previous, "이전 질문을 현재 범위·근거·이수학점으로 다시 확인할 수 없어 질문을 구체화해야 합니다.", status=status), False, []
        rules = [self.engine.registry.rules[item.rule_id] for item in prior.evidence_packet.applied_rules]
        if any(rule["decision"]["outcome"]["type"] not in _CONTEXT_OUTCOME_TYPES for rule in rules):
            return self._refuse_followup(request, previous, "운영정책·면제·대체 관계는 후속 문맥으로 확정하지 않습니다. 확인하려는 정책을 직접 질문해 주세요."), False, []
        if len(prior.intent_ids) != 1 or len(rules) != 1:
            candidates = prior.intent_ids
            if len(candidates) == 1:
                rule_ids = {item.rule_id for item in prior.evidence_packet.applied_rules}
                candidates = [entry["intent_id"] for entry in self.engine.registry.intents["intents"]
                              if entry["kind"] == "specific" and len(entry["rule_ids"]) == 1 and entry["rule_ids"][0] in rule_ids]
                candidates.sort(key=lambda item: (0 if item.endswith(".total") else 1, item))
            return self._refuse_followup(request, previous, "이전 질문에 여러 기준이 있어 후속 질문의 대상을 선택해야 합니다."), False, self._choices(candidates, gap=kind == "gap")
        outcome = rules[0]["decision"]["outcome"]
        expected = {"gap": outcome["type"] == "credit_threshold", "credits": outcome["type"] == "credit_threshold",
                    "courses": outcome["type"] == "coverage_requirement" and outcome.get("coverage_kind") == "course_set",
                    "count": outcome["type"] == "completion_requirement" and "minimum_completions" in outcome,
                    "explain": True}[kind]
        if not expected:
            return self._refuse_followup(request, previous, "이전 기준과 질문의 학점·과목·횟수 단위가 맞지 않습니다. 확인하려는 항목을 직접 질문해 주세요."), False, []
        entry = next(entry for entry in self.engine.registry.intents["intents"] if entry["intent_id"] == prior.intent_ids[0])
        resolved = AcademicAnswerRequest(question=entry["aliases"][0] + (" 부족학점" if kind == "gap" else " 기준"),
            admission_year=request.admission_year, matched_curriculum_year=request.matched_curriculum_year,
            department=request.department, earned_credits=dict(request.earned_credits))
        return self.engine.answer(resolved), True, []

    def _eligible(self, base: AcademicAnswerResponse, question: str) -> bool:
        compact = unicodedata.normalize("NFKC", question).lower().replace(" ", "")
        return (
            base.status == "insufficient_evidence"
            and base.answer == _NO_MATCH_ANSWER
            and base.intent_ids == []
            and base.evidence_packet.applied_rules == []
            and base.evidence_packet.evidence == []
            and not any(term in compact for term in _UNVERIFIED_OPERATION_TERMS)
            and not any(alias in compact for alias in self.engine.registry.intents["protected_aliases"])
            and _safe_for_model(question)
        )
