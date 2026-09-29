"""Fail-closed chat facade around the deterministic academic answer engine."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from .core import AnswerEngine
from .llm import ALLOWED_INTENT_SIGNALS, IntentSuggester, LLMInvalidResponse, LLMUnavailable, LocalLLMClient
from .models import AcademicAnswerRequest, AcademicAnswerResponse


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
    """Adds only a static follow-up suggestion to an unchanged academic answer.

    A model is invoked solely when the trusted engine found no intent. An LLM
    output never changes status, answer, calculations, applied rules, or cites.
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

    def chat(self, request: AcademicAnswerRequest) -> GroundedChatResponse:
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
        })

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
