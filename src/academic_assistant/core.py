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
_GAP_WORDS = ("부족", "모자", "남았", "남은", "남아", "더 들어", "더 이수", "더 필요")
_UNSAFE_QUESTION = re.compile(r"(?:이름|성명|학번|학생번호|주민등록|성적표|raw\s*transcript|student\s*(?:name|id|number)|[0-9]{6,12}|01[016789][\s.-]*[0-9]{3,4}[\s.-]*[0-9]{4}|[\w.+-]+@[\w.-]+\.[a-z]{2,})", re.IGNORECASE)
_ADMISSION_COHORT_PHRASE = re.compile(r"(?<![0-9])(?:(?:19|20|21)[0-9]{2}|[0-9]{2})\s*학번(?![0-9])")
_KOREAN_SURNAME = r"(?:김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권|황|안|송|전|홍|유|고|문|양|손|배|백|허|남|심|노|하|곽|성|차|주|우|구|민|진|지|엄|채|원|천|방|공|현|함|변|염|여|추|도|소|석|선|설|마|길|연|위|표|명|기|반|왕|금|옥|육|인|맹|제|모|탁|국|어|은|편|용)"
_KOREAN_COMPOUND_SURNAME = r"(?:남궁|황보|제갈|선우|사공|독고|동방|서문|남문)"
_HIGH_PRECISION_SURNAME = r"(?:김|박|정|홍)"
_NAME_PARTICLE = r"(?:의|은|는|이|가|을|를|도|에)"
_KOREAN_NAME_WITH_HUMAN_SUFFIX = re.compile(
    rf"(?<![가-힣]){_KOREAN_SURNAME}[가-힣]{{2}}(?:에게|한테|씨|님)(?![가-힣])"
)
_KOREAN_NAME_WITH_PARTICLE = re.compile(
    rf"(?<![가-힣]){_HIGH_PRECISION_SURNAME}[가-힣]{{2}}{_NAME_PARTICLE}(?![가-힣])"
)
_KOREAN_COMPOUND_NAME_WITH_PARTICLE = re.compile(
    rf"(?<![가-힣]){_KOREAN_COMPOUND_SURNAME}[가-힣]{{2}}(?:에게|한테|씨|님|의|은|는|이|가|을|를|도|에)(?![가-힣])"
)
_KOREAN_BARE_NAME_TOKEN = re.compile(r"김[가-힣]{2}")
_KOREAN_COMPOUND_BARE_NAME_TOKEN = re.compile(rf"(?<![가-힣])(?P<name>{_KOREAN_COMPOUND_SURNAME}[가-힣]{{2}})(?![가-힣])")
_LATIN_FULL_NAME = re.compile(r"(?<![A-Za-z])[A-Z][a-z]{1,30}[ \t]+[A-Z][a-z]{1,30}(?![A-Za-z])")
_LATIN_LOWERCASE_NAME = re.compile(
    r"(?<![A-Za-z])(?:john|jane|james|mary|michael|david|alice|robert|sarah|anna)[ \t]+[a-z]{2,30}(?![A-Za-z])",
    re.IGNORECASE,
)
_BARE_NAME_ACADEMIC_CONTEXT = ("휴학", "복학", "전과", "재입학", "학적", "졸업", "학점", "수강", "성적", "이수", "과목", "강의")
_BARE_NAME_GRAMMATICAL_ENDINGS = ("는", "가", "을", "를", "의", "도", "에", "게", "요", "어", "된", "상", "없", "하", "들", "면", "고", "해", "할", "했")
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
_EXPLICIT_DEPARTMENT = re.compile(r"(?<![가-힣])([가-힣]{1,30}(?:학과|학부))")
_EXPLICIT_DISCIPLINE = re.compile(r"(?<![가-힣])([가-힣]{2,30}공학)(?=(?:은|는|이|가|의|도|만|을|를|에서|으로|로|인|과|와)?(?:\s|$))")
_EXPLICIT_SHORT_DEPARTMENT = re.compile(r"(?<![가-힣])([가-힣]{2,20}과)(?=(?:은|는|이|가|의|도|만|을|를|에서|으로|로|인|라면|인가요)?(?:\s|$))")
_ACADEMIC_KWA_STEMS = ("전공", "교양", "졸업", "논문", "학점", "교과", "과목", "이수", "수강", "기준", "요건", "규정", "정책", "학기", "학년", "과정", "교육")
_NON_DEPARTMENT_KWA_WORDS = frozenset({"미통과", "불통과"})
_UNSUPPORTED_DEPARTMENT_MARKERS = ("다른학과", "타학과", "다른학부", "타학부", "타과", "학과별", "학부별", "타전공", "다른전공")
_EXPLICIT_YEAR = re.compile(r"(?<![0-9])((?:19|20|21)[0-9]{2})(?![0-9])")
_EXPLICIT_SHORT_COHORT = re.compile(r"(?<![0-9])([0-9]{2})\s*(?:학번|학년도|년도?\s*입학|교육과정)")
_APPROVED_SUBSTITUTION_INTENTS = frozenset({
    "graduation.thesis.substitution", "course-counting.post-completion-equivalence",
})
_LINKED_THESIS_INTENT_ID = "graduation.thesis.linked-program-exemption"
# Only complete, general-policy questions can cross the protected-topic guard.
# These grammars do not accept scores, student records, exemptions, additional
# requirements, or a promise about a future PCCP threshold.
_OPERATIONAL_SCOPE_PREFIX = r"(?:(?:(?:2026|26)학번)?컴퓨터공학과|(?:2026|26)학번)?"
_POLICY_QUESTION_SUFFIX = (
    r"(?:은|는|이|가|을|를)?"
    r"(?:무엇인가요|무엇이죠|어떻게되나요|어떻게처리되나요|"
    r"알려주세요|알려줘|설명해주세요|설명해줘|몇점인가요|"
    r"몇점이상인가요|얼마인가요)?"
)
_CAPSTONE_I = r"(?:캡스톤(?:디자인)?(?:i|1)|캡디(?:i|1))"
_CAPSTONE_II = r"(?:캡스톤(?:디자인)?(?:ii|2)|캡디(?:ii|2))"
_CODING_FAILURE = r"(?:코딩테스트|코테)(?:에)?(?:미통과(?:시|하면)?|불합격(?:시|하면)?|(?:를)?통과하지못하면)"
_OPERATIONAL_QUESTION_PATTERNS = {
    "operations.pccp-current-trial": (
        re.compile(_OPERATIONAL_SCOPE_PREFIX + r"(?:현재|현행|시범운영|현행시범)?pccp(?:의)?(?:합격|통과)(?:점수|기준)" + _POLICY_QUESTION_SUFFIX),
        re.compile(_OPERATIONAL_SCOPE_PREFIX + r"(?:현재|현행)?pccp(?:에)?(?:합격|통과)하려면몇점(?:이상)?(?:필요한가요|필요해요|이어야하나요)"),
    ),
    "operations.coding-test-failure": (
        re.compile(_OPERATIONAL_SCOPE_PREFIX + _CODING_FAILURE + _POLICY_QUESTION_SUFFIX),
        re.compile(_OPERATIONAL_SCOPE_PREFIX + _CODING_FAILURE + r"(?:처리|조치|캡스톤처리)" + _POLICY_QUESTION_SUFFIX),
        re.compile(_OPERATIONAL_SCOPE_PREFIX + _CODING_FAILURE + _CAPSTONE_I + r"(?:성적|결과|처리)?" + _POLICY_QUESTION_SUFFIX),
        re.compile(_OPERATIONAL_SCOPE_PREFIX + _CODING_FAILURE + r"다음연도" + _CAPSTONE_II + r"(?:수강|수강제한)" + _POLICY_QUESTION_SUFFIX),
    ),
    "operations.graduation-work-prerequisite": (
        re.compile(_OPERATIONAL_SCOPE_PREFIX + r"졸업작품(?:의)?수강(?:선행조건|선수요건|전제조건)" + _POLICY_QUESTION_SUFFIX),
        re.compile(_OPERATIONAL_SCOPE_PREFIX + _CAPSTONE_II + r"(?:를)?(?:pass|패스|통과)해야(?:만)?졸업작품(?:을)?수강(?:할수있나요|해야하나요|하나요)"),
        re.compile(_OPERATIONAL_SCOPE_PREFIX + _CAPSTONE_II + r"(?:pass|패스|통과)후졸업작품수강(?:관계|선행조건)" + _POLICY_QUESTION_SUFFIX),
    ),
}
_PARAPHRASE_PATTERNS = {
    "credits.graduation.total": (
        re.compile(r"졸업(?:하려면|을하려면|에필요한)(?:총)?(?:몇|얼마나많은)학점(?:을|이)?(?:이수해야하나요|필요한가요|필요해요|들어야하나요|채워야하나요)"),
        re.compile(r"졸업(?:은|에)?총몇학점(?:이|을)?(?:필요한가요|필요해요|이수해야하나요|채워야하나요)"),
    ),
    "major.required-course-set": (
        re.compile(r"(?:전공필수|전필|필수전공)(?:는|가|은)?(?:몇|어떤)과목(?:을)?(?:인가요|이야|있나요|이수해야하나요|들어야하나요)"),
    ),
    "major.counseling-completion": (
        re.compile(r"심층상담(?:은|을)?몇(?:번|회)(?:을)?(?:이수해야하나요|해야하나요|해야해요|해야해|인가요|이야)"),
    ),
    "graduation.thesis.required": (
        re.compile(r"졸논(?:은|이)?(?:필수인가요|필수야|이수해야하나요|들어야하나요)"),
    ),
    "graduation.thesis.completion-result": (
        re.compile(r"졸논(?:이|은)?0학점(?:이어도|인데)(?:반드시)?(?:이수해야하나요|들어야하나요|필수인가요)"),
    ),
}
# A linked-program thesis exemption is a narrow approved topic, not a general
# benefit/eligibility query. Consume the whole question before citing its rule.
_LINKED_THESIS_TOPIC = r"(?:학석사)?연계과정(?:생(?:은|의))?(?:졸업)?논문면제"
_LINKED_THESIS_DIRECT_SUFFIX = re.compile(
    r"(?:가능성이있나요|가능한가요|조건|요건|정책은무엇인가요|"
    r"(?:혜택)?적용정책을알려주세요)?"
)
_LINKED_THESIS_INSTITUTIONAL_SUFFIX = re.compile(r"가능성이있나요")
_LINKED_THESIS_POLICY_SUFFIX = re.compile(
    r"(?:적용정책을(?:알고싶어요|알려주세요)|"
    r"정책(?:이누구에게적용되는지알려주세요|"
    r"은어떻게적용되는지알고싶어요|"
    r"에따르면누가대상인지알고싶어요|"
    r"상대상범위를알고싶어요|"
    r"에대한설명을부탁드립니다|"
    r"의적용범위를알고싶어요))"
)
_LINKED_THESIS_QUESTION_SUFFIX = re.compile(
    r"(?:대상일반정책(?:입니다|인지확인하고싶어요)|"
    r"정책이어떻게적용되는지입니다)"
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
# Aliases identify subjects, not entire questions. Once those spans are removed,
# every remaining character must belong to a known complete query grammar.
# In particular no arbitrary deadline, location, procedure or extra clause may
# be discarded merely because an approved subject also appeared.
_QUERY_PREFIX = re.compile(
    r"(?:저는|제가|제질문은|주제는|과제관련질문은|문제없이)?"
    r"(?:(?:2026|26)(?:학번|년도?입학|년에입학했는데|교육과정))?"
    r"(?:컴퓨터공학과)?(?:현재교육과정에)?"
)
_QUERY_PARTICLE = r"(?:은|는|이|가|을|를|의)?"
_QUERY_META = r"(?:최소)?(?:학점)?(?:기준학점|기준|합계|요건|정책|이수기준|이수결과|결과)?"
_DIRECT_QUERY = re.compile(
    _QUERY_PARTICLE + _QUERY_META + _QUERY_PARTICLE
    + r"(?:알려줘|알려주세요|설명해줘|설명해주세요|궁금해|궁금해요|"
      r"무엇인가요|무엇인지알려주세요|있나요|적혀있나요|어떻게되나요)?"
)
_CREDIT_QUERY = re.compile(
    _QUERY_PARTICLE + _QUERY_META + _QUERY_PARTICLE
    + r"(?:(?:최소)?몇학점(?:기준)?(?:인가요|이야|필요해요|필요한가요)?|"
      r"얼마(?:죠|인가요)|얼마나필요한가요|몇학점더필요|"
      r"(?:현재[0-9]{1,3}학점인데)?(?:몇학점더필요|얼마나(?:부족해|부족한가요|남았어|남았나요))|"
      r"부족학점|부족한학점|남은학점)"
)
_BOOLEAN_QUERY = re.compile(
    _QUERY_PARTICLE + r"(?:0학점이어도)?(?:필수야|필수인가요|이수해야하나요|들어야하나요|필요한가요)"
)
_COMPLETION_QUERY = re.compile(
    r"(?:졸업하려면)?" + _QUERY_PARTICLE
    + r"(?:몇(?:번|회)(?:를|을)?이수해야하나요|0학점(?:이어도)?(?:필요한가요)?|"
      r"꼭수강해야하나요|안들으면fail인가요|필수야|필수인가요|이수해야하나요)"
)
_COVERAGE_QUERY = re.compile(
    _QUERY_PARTICLE
    + r"(?:목록|어떤과목이있나요|몇과목(?:인가요|이야|들어야하나요)|"
      r"무엇인지알려주세요|서로다른영역이어야하나요)"
)
_COUNTING_QUERY = re.compile(
    r"(?:(?:두)?(?:기이수)?(?:과목|교과목|수업|강의)(?:들)?(?:의|은|이)?)?"
    + _QUERY_PARTICLE + r"(?:으로지정(?:된경우|되면))?(?:"
      r"(?:학점(?:을|이|은)?)?(?:중복)?(?:어떻게)?계산(?:하나요|되나요)?|"
      r"예전에받은학점도같이계산되나요|두번들으면학점이중복되나요|"
      r"별도학점으로보나요|"
      r"소급적용되면(?:일반적으로)?어떻게계산하나요|"
      r"(?:소급(?:적용)?(?:일반)?|(?:일반)?(?:계산)?|학점)정책(?:"
        r"입니다|인지확인하고싶어요|을(?:알려줘|알려주세요|알고싶어요)|"
        r"에따른(?:학점)?계산방법을알려주세요|"
        r"의(?:학점계산방법을|적용범위를)알고싶어요|"
        r"은어떻게적용되는지알고싶어요|에대해알고싶어요))"
)
_COHORT_QUERY = re.compile(_QUERY_PARTICLE + r"(?:교육과정|어느입학연도(?:의)?교육과정을적용하나요)")


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


def _question_exceeds_scope(question: str) -> bool:
    compact = question.replace(" ", "")
    # "다른 전공학점" can refer to another credit component in the same
    # curriculum, not another department. Explicit department/cohort entities
    # below and all other cross-department markers still retain their bounds.
    department_text = compact.replace("다른전공학점", "전공학점")
    if any(marker in department_text for marker in _UNSUPPORTED_DEPARTMENT_MARKERS):
        return True
    if any(int(year) != 2026 for year in _EXPLICIT_YEAR.findall(question)):
        return True
    if any(int(year) != 26 for year in _EXPLICIT_SHORT_COHORT.findall(question)):
        return True
    if any(department != "컴퓨터공학과" for department in _EXPLICIT_DEPARTMENT.findall(question)):
        return True
    if any(discipline != "컴퓨터공학" for discipline in _EXPLICIT_DISCIPLINE.findall(question)):
        return True
    return any(
        department != "컴퓨터공학과" and department not in _NON_DEPARTMENT_KWA_WORDS
        and not any(department[:-1].startswith(stem) for stem in _ACADEMIC_KWA_STEMS)
        for department in _EXPLICIT_SHORT_DEPARTMENT.findall(question)
    )


def _asks_if_one_requirement_is_enough_for_graduation(compact: str) -> bool:
    return bool(
        re.search(r"만.{0,80}졸업(?:되|가능|할수|하나|해)", compact)
        or re.search(r"졸업하려면.{0,80}만.{0,30}(?:되|충분|가능)", compact)
    )


def _is_approved_linked_thesis_question(compact: str) -> bool:
    for prefix, suffix in (
        ("", _LINKED_THESIS_DIRECT_SUFFIX),
        ("제도상", _LINKED_THESIS_INSTITUTIONAL_SUFFIX),
        ("저는", _LINKED_THESIS_POLICY_SUFFIX),
        ("제질문은", _LINKED_THESIS_QUESTION_SUFFIX),
    ):
        if not compact.startswith(prefix):
            continue
        topic = re.match(_LINKED_THESIS_TOPIC, compact[len(prefix):])
        if topic and suffix.fullmatch(compact[len(prefix) + topic.end():]):
            return True
    return False


def _asks_unapproved_exemption(compact: str) -> bool:
    return "면제" in compact and not _is_approved_linked_thesis_question(compact)


def validate_request_safety(payload: AcademicAnswerRequest) -> None:
    validate_public_text_safety(payload.question)
    if set(payload.earned_credits) - APPROVED_METRICS:
        raise ValueError("unknown earned-credit metric")


def validate_public_text_safety(value: str) -> None:
    normalized = unicodedata.normalize("NFKC", value)
    # A two- or four-digit admission cohort is not an individual student identifier.
    # Keep the full text for every other PII check, including 6-12 digit IDs.
    without_cohort_label = _ADMISSION_COHORT_PHRASE.sub(lambda match: match.group().replace("학번", ""), normalized)
    # A course's name label is public academic vocabulary, not a person's
    # name. Remove only that qualified label from this one lexical check;
    # original text still undergoes all person/identifier checks below.
    without_course_label = re.sub(r"((?:교과목|과목|강의)(?:의)?\s*)이름", r"\1label", without_cohort_label)
    if _UNSAFE_QUESTION.search(without_course_label) or any(ord(char) < 32 and char not in "\t\n\r" for char in normalized):
        raise ValueError("unsafe or identifying question content")
    if without_course_label != without_cohort_label:
        # The qualified-label exception must not make a supplied human name
        # safe merely because it appears beside a course-label request.
        for token in re.findall(r"[가-힣]+", without_course_label):
            if re.fullmatch(rf"(?:{_KOREAN_SURNAME}[가-힣]{{2}}|{_KOREAN_COMPOUND_SURNAME}[가-힣]{{2}})(?:의|은|는|이|가|씨|님|이라고|라는|라고)?", token):
                raise ValueError("unsafe or identifying question content")
    if any(pattern.search(normalized) for pattern in (
        _KOREAN_NAME_WITH_HUMAN_SUFFIX,
        _KOREAN_NAME_WITH_PARTICLE,
        _KOREAN_COMPOUND_NAME_WITH_PARTICLE,
    )):
        raise ValueError("unsafe or identifying question content")
    if _LATIN_FULL_NAME.search(normalized) or _LATIN_LOWERCASE_NAME.search(normalized):
        raise ValueError("unsafe or identifying question content")
    tokens = re.findall(r"[가-힣]+", normalized)
    for index, candidate in enumerate(tokens):
        is_compound_name = _KOREAN_COMPOUND_BARE_NAME_TOKEN.fullmatch(candidate) is not None
        if not is_compound_name and not _KOREAN_BARE_NAME_TOKEN.fullmatch(candidate):
            continue
        if not is_compound_name and candidate.endswith(("생", "자", "과", *_BARE_NAME_GRAMMATICAL_ENDINGS)):
            continue
        neighbors = (*tokens[max(0, index - 2):index], *tokens[index + 1:index + 2])
        if any(context in neighbor for neighbor in neighbors for context in _BARE_NAME_ACADEMIC_CONTEXT):
            raise ValueError("unsafe or identifying question content")


class AnswerEngine:
    def __init__(self, registry: Registry | None = None, *, evidence_reader=None) -> None:
        self.registry = registry or Registry.load()
        self.evidence_reader = evidence_reader

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
        if _question_exceeds_scope(question):
            safe_scope = {**scope, "department": SANITIZED_DEPARTMENT}
            return self._unsupported(packet_id, safe_scope, "out_of_scope", "질문에 지정된 학번·학과가 지원 범위와 다릅니다.", "scope")
        config = self.registry.intents
        operational = self._select_operational_policy(compact)
        if any(alias in compact for alias in config["protected_aliases"]) and not operational:
            return self._unsupported(packet_id, scope, "insufficient_evidence", "해당 운영 관행은 공식 근거가 확인되지 않아 답변할 수 없습니다.", "review")
        if any(alias in compact for alias in config["exception_aliases"]):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "해당 예외 적용에는 별도의 승인된 근거가 필요합니다.", "missing")
        if self._is_individual_determination(question, compact):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "개인별 이수·면제·소급 적용 결과를 판정하려면 공식 학적 확인이 필요합니다.", "missing")
        if _asks_unapproved_exemption(compact):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "질문한 면제 관계를 뒷받침할 승인된 근거가 없습니다.", "missing")
        if "재수강" in compact and "소급" in compact:
            return self._unsupported(packet_id, scope, "insufficient_evidence", "재수강의 소급 적용 여부를 판정할 승인 근거가 없습니다.", "missing")
        if _asks_if_one_requirement_is_enough_for_graduation(compact):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "하나의 이수요건만으로 졸업 가능 여부를 판정할 근거가 충분하지 않습니다.", "missing")
        if any(alias in compact for alias in config["broad_graduation_aliases"]):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "포괄적인 졸업 인증 여부를 판정할 승인 근거가 충분하지 않습니다.", "missing")
        specific_aliases = [alias for entry in config["intents"] if entry["kind"] == "specific" for alias in entry["aliases"]]
        if any(alias in compact for alias in config["negation_aliases"]) or any(f"비{alias}" in compact for alias in specific_aliases):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "부정 또는 대비 표현이 포함되어 적용할 규칙을 확정할 수 없습니다.", "missing")
        paraphrase = self._select_paraphrase(compact)
        preliminary = operational or paraphrase or self._select_intents(compact, question)
        if any(intent["intent_id"] == _LINKED_THESIS_INTENT_ID for intent in preliminary) and not _is_approved_linked_thesis_question(compact):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "질문 전체가 승인된 논문 면제 정책 범위에 해당하지 않습니다.", "missing")
        if "대체" in compact and (
            not preliminary or any(intent["intent_id"] not in _APPROVED_SUBSTITUTION_INTENTS for intent in preliminary)
        ):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "질문한 대체 관계를 뒷받침할 승인된 근거가 없습니다.", "missing")
        if preliminary and any(alias in compact for alias in config["disjunction_aliases"]):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "선택형 표현이 포함되어 적용할 규칙을 확정할 수 없습니다.", "missing")
        if len(preliminary) > 1 and not self._has_explicit_conjunction(preliminary, question):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "여러 규칙을 연결하는 승인된 접속 표현이 없어 적용 대상을 확정할 수 없습니다.", "missing")
        if preliminary and not operational and not paraphrase and not self._whole_question_supported(preliminary, question):
            return self._unsupported(packet_id, scope, "insufficient_evidence", "질문에 승인된 근거로 확인할 수 없는 추가 조건이나 절차가 포함되어 있습니다. 확인하려는 요건을 나누어 질문해 주세요.", "missing")
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
        gap_requested = any(word in question for word in _GAP_WORDS)
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
            statement = rule["decision"]["statement"]
            answer_statement = statement
            outcome = rule["decision"]["outcome"]
            if outcome["type"] == "coverage_requirement" and outcome.get("requirement") == "major.required.course_set":
                answer_statement += " 지정 과목은 " + ", ".join(item["label"] for item in outcome["items"]) + "이다."
            statements.append(answer_statement)
            for item in rule["evidence"]:
                evidence.append({"source_id": item["source_id"], "rule_id": rule_id, "locator": item["locator"], "claim": statement})
        answer_text = " ".join(statements)
        if self.evidence_reader is not None:
            evidence = self.evidence_reader.fetch_evidence(rule_ids, self.registry)
        if calculations:
            answer_text += " " + " ".join(f"{c['metric']}은(는) {c['earned']}학점 이수하여 {c['gap']}학점이 부족합니다." for c in calculations)
        packet = {"schema_version": "2.0.0", "packet_id": packet_id, "scope": scope, "student_facts": dict(request.earned_credits), "applied_rules": applied, "evidence": evidence, "issues": [], "status": "supported"}
        return AcademicAnswerResponse(packet_id=packet_id, status="supported", answer=answer_text, intent_ids=intent_ids, calculations=calculations, evidence_packet=packet)

    def _select_operational_policy(self, compact: str) -> list[dict[str, Any]]:
        matched = [
            intent_id for intent_id, patterns in _OPERATIONAL_QUESTION_PATTERNS.items()
            if any(pattern.fullmatch(compact) for pattern in patterns)
        ]
        if len(matched) != 1:
            return []
        return [entry for entry in self.registry.intents["intents"] if entry["intent_id"] == matched[0]]

    def _select_paraphrase(self, compact: str) -> list[dict[str, Any]]:
        matched = [intent_id for intent_id, patterns in _PARAPHRASE_PATTERNS.items()
                   if any(pattern.fullmatch(compact) for pattern in patterns)]
        if len(matched) != 1:
            return []
        return [entry for entry in self.registry.intents["intents"] if entry["intent_id"] == matched[0]]

    def _whole_question_supported(self, entries: list[dict[str, Any]], normalized: str) -> bool:
        compact = normalized.replace(" ", "")
        if len(entries) == 1 and entries[0]["intent_id"] == _LINKED_THESIS_INTENT_ID:
            return _is_approved_linked_thesis_question(compact)
        spans = [span for entry in entries for span in self._entry_alias_spans(entry, normalized)]
        remainder = "".join(char for offset, char in enumerate(compact)
                            if not any(start <= offset < end for start, end in spans))
        prefix = _QUERY_PREFIX.match(remainder)
        remainder = remainder[prefix.end():]
        if len(entries) > 1:
            # Explicit conjunction structure was checked before this method.
            remainder = re.sub(r"^(?:및|그리고|과|와)+", "", remainder)
        if len(entries) == 1 and entries[0]["intent_id"] == "credits.major.elective" and "전선" in compact:
            # The shorthand has an explicit registered academic-context cue.
            remainder = re.sub(r"^전공", "", remainder)
        if _DIRECT_QUERY.fullmatch(remainder):
            return True
        types = {self.registry.rules[rule_id]["decision"]["outcome"]["type"]
                 for entry in entries for rule_id in entry["rule_ids"]}
        patterns = []
        if types <= {"credit_threshold", "credit_recognition_cap"}:
            patterns.append(_CREDIT_QUERY)
        if types == {"boolean_requirement"}:
            patterns.append(_BOOLEAN_QUERY)
        if types == {"completion_requirement"}:
            patterns.append(_COMPLETION_QUERY)
        if types == {"coverage_requirement"}:
            patterns.append(_COVERAGE_QUERY)
        if types == {"course_counting_policy"}:
            patterns.append(_COUNTING_QUERY)
        if types == {"cohort_assignment_policy"}:
            patterns.append(_COHORT_QUERY)
        return any(pattern.fullmatch(remainder) for pattern in patterns)

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
