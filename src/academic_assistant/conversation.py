"""Bounded Korean conversation grammar and deterministic evidence presentation.

Rewriting consumes the complete query. It selects approved topics, never facts,
and every resolved question is replayed by the unchanged academic engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .core import _question_exceeds_scope, _asks_if_one_requirement_is_enough_for_graduation, normalize_text
from .models import AcademicAnswerRequest, AcademicAnswerResponse

_PARTICLE = r"(?:에대해|에대한|관련해서|관련된|관련|은|는|이|가|을|를|의)?"
_META = r"(?:(?:최소|총|필요한|이수)?(?:이수)?학점|학점기준|기준학점|이수기준|기준|요건|규정|정책|조건|내용|목록|계산방법|적용연도|교육과정)?"
_ASK = (r"(?:좀|조금)?(?:쉽게|친절하게|자세히|간단히)?(?:알려|설명해|정리해|안내해)"
        r"(?:줘|주세요|줄래|줄래요|주실래요|주실수있을까요|주실수있나요|줄수있나요|주시면좋겠어요)")
_ASK = rf"(?:{_ASK}|부탁드려요|부탁드립니다)"
_PRESENTATION = r"(?:함께|각각|전체적으로|항목별로)?"
_TAIL = re.compile(_PARTICLE + _META + _PARTICLE + _PRESENTATION + rf"(?:{_ASK}|궁금해요|궁금해|알고싶어요|무엇인가요|뭔가요|어떻게되나요|어떻게되는지궁금해요)?")
_CREDIT_TAIL = re.compile(_PARTICLE + _PRESENTATION + r"(?:최소|총|최대)?(?:몇학점|얼마나)(?:이|을)?(?:인가요|이야|필요한가요|필요해요|필요해|이수해야하나요|이수해야해요|들어야하나요|들어야해요|채워야하나요|채워야해요)?")
_GAP_TAIL = re.compile(_PARTICLE + r"(?:몇학점(?:이|을)?(?:더)?|얼마나(?:더)?)(?:부족해|부족한가요|남았어|남았나요|남아요|필요해요|필요한가요)(?:요)?")
_SYNONYMS = {
    "credits.graduation.total": ("졸업총이수학점", "전체졸업학점"),
    "credits.general.total": ("교양전체", "전체교양학점"),
    "credits.major.total": ("전공전체", "전체전공학점"),
    "credits.major.required": ("전공필수학점", "전필학점"),
    "credits.major.elective": ("전공선택학점", "전선학점"),
    "credits.major.minimum": ("전공최소학점",),
    "credits.general.foundation": ("기초교양학점",),
    "credits.general.balanced": ("균형교양학점",),
    "credits.general.remaining": ("남는교양학점",),
    "credits.general.recognition-cap": ("교양인정상한", "교양학점인정상한", "졸업학점에반영되는교양최대학점"),
    "credits.general.remaining-allocation": ("교양잔여학점배분",),
    "credits.graduation.remaining-allocation": ("졸업잔여학점배분", "자유선택학점배분"),
    "credits.graduation.remaining": ("자유선택학점",),
    "general.balanced-area-coverage": ("균형교양영역", "균형교양4영역"),
    "general.recommended-courses": ("추천교양과목", "교양추천과목"),
    "major.required-course-set": ("전공필수교과목", "전공필수강의", "전공필수과목들"),
    "major.counseling-completion": ("심층상담이수횟수", "상담이수횟수"),
    "graduation.thesis.linked-program-exemption": ("학석사연계과정졸업논문면제", "학석사연계과정논문면제", "연계과정논문면제정책"),
    "graduation.thesis.substitution": ("졸업논문대체규정",),
    "graduation.thesis.completion-result": ("0학점졸업논문",),
    "course-counting.identical-course": ("동일과목학점계산",),
    "course-counting.post-completion-equivalence": ("이수후동일대체지정학점",),
    "course-counting.retake": ("재수강학점계산",),
    "cohort.department-transfer.original-admission-year": ("전과생교육과정", "전과적용연도"),
    "operations.pccp-current-trial": ("현재pccp합격기준", "현행시범pccp합격기준", "현재pccp통과점수"),
    "operations.coding-test-failure": ("코딩시험미통과처리", "코딩테스트불합격처리", "코테불합격처리"),
    "operations.graduation-work-prerequisite": ("졸업작품선수조건", "졸업작품선행조건"),
}
_CANONICAL = {
    "graduation.thesis.linked-program-exemption": "학석사연계과정 졸업논문 면제 적용정책을 알려주세요",
    "operations.pccp-current-trial": "현행 PCCP 합격 기준 알려주세요",
    "operations.coding-test-failure": "코딩 테스트 미통과 시 처리 알려주세요",
    "operations.graduation-work-prerequisite": "졸업작품 수강 선행조건 알려주세요",
}
_LABELS = {
    "credits.graduation.total": "졸업 총학점", "credits.general.total": "교양 총학점",
    "credits.general.foundation": "기초교양", "credits.general.balanced": "균형교양",
    "credits.general.remaining": "교양 잔여 학점", "credits.major.required": "전공필수",
    "credits.major.elective": "전공선택", "credits.major.minimum": "최소전공",
    "credits.major.advanced": "심화전공", "credits.major.total": "전공 총학점",
    "credits.graduation.remaining": "졸업 잔여 학점",
}
_CREDIT_TOPICS = {
    "credits.graduation.total": r"졸업(?:에필요한(?:전체|총)?학점|하려면(?:총)?|총(?:이수)?학점)",
    "credits.general.total": r"교양(?:전체(?:이수)?(?:학점)?|을모두합쳐서|총(?:이수)?학점)",
    "credits.major.total": r"전공(?:전체(?:이수)?(?:학점)?|총(?:이수)?학점|이수학점소계)",
    "credits.general.foundation": r"기초교양", "credits.general.balanced": r"균형교양",
    "credits.general.remaining": r"교양잔여(?:학점)?", "credits.major.required": r"(?:전공필수|전필)",
    "credits.major.elective": r"(?:전공선택|전선)", "credits.major.minimum": r"최소전공",
    "credits.major.advanced": r"심화전공", "credits.graduation.remaining": r"졸업잔여(?:학점)?",
}
_CREDIT_FRAME_END = (r"(?:(?:최소|총)?(?:몇학점|얼마나|얼마)(?:이|을)?(?:인가요|이야|필요해요|필요한가요|이수해야하나요|이수해야하는지궁금해요|들어야하나요|들어야하는지궁금해요|채워야하나요)|"
                     rf"(?:필요한)?학점(?:의)?(?:소계|기준)?{_PARTICLE}(?:{_ASK})|(?:{_ASK}))")
_COURSE_NOUN = r"(?:과목|교과목|강의)(?:들)?"
_COURSE_COMPLETION = r"(?:들어야|이수해야|수강해야)"
_COURSE_SET_FRAME = (rf"(?:전공필수|전필|필수전공)(?:(?:로|에는|에|의)?어떤{_COURSE_NOUN}(?:을|이|은|는)?"
                     rf"(?:{_COURSE_COMPLETION}(?:하나요|해요)|있나요)|{_COURSE_NOUN}(?:으로|로)"
                     rf"(?:무엇|어떤{_COURSE_NOUN})(?:을)?{_COURSE_COMPLETION}(?:하나요|하는지{_ASK})|"
                     rf"(?:로|에는|에)?{_COURSE_COMPLETION}하는{_COURSE_NOUN}{_PARTICLE}(?:무엇인가요|뭔가요|{_ASK}))")
_ZERO_CREDITS = r"(?:0|영)학점"
_THESIS = r"(?:졸업논문|졸논)"
_MANDATORY_COMPLETION = (rf"(?:반드시|꼭)?(?:{_COURSE_COMPLETION}(?:하나요|해요|하는지{_ASK})|"
                         r"이수해야합니까)")
_ZERO_THESIS_FRAME = (rf"(?:{_THESIS}(?:이|은|가)?{_ZERO_CREDITS}(?:이어도|인데|인경우에도|이라도)|"
                      rf"{_ZERO_CREDITS}{_THESIS}(?:도|이|은)?)" + _MANDATORY_COMPLETION)
_PCCP = r"(?:pccp|피씨씨피)"
_CURRENT_PCCP = rf"(?:현행|현재(?:시행중인)?)(?:시범)?{_PCCP}"
_SCORE_QUESTION = rf"(?:최소)?몇점(?:이상)?(?:인가요|필요한가요)|{_ASK}"
_PCCP_SCORE_FRAME = (rf"(?:{_CURRENT_PCCP}|{_PCCP})(?:의)?(?:합격|통과)(?:(?:기준)?점수|기준)"
                     rf"{_PARTICLE}(?:{_SCORE_QUESTION})|(?:{_CURRENT_PCCP}|{_PCCP})(?:의)?"
                     r"(?:합격|통과)하려면점수(?:가|는)?얼마나(?:되어야|돼야|필요한가요)(?:하나요)?")
_FRAMES = {
    "cohort.department-transfer.original-admission-year": rf"전과(?:(?:한학생|학생|생)(?:의)?교육과정(?:의)?적용(?:연도|학년도){_PARTICLE}(?:{_ASK}|궁금해요)|했을때어느입학연도(?:의)?교육과정을따르는지(?:{_ASK}|궁금해요))",
    "course-counting.identical-course": rf"(?:(?:같은교과목을여러번들었을때학점은|동일교과목으로지정된교과목의학점)(?:을)?(?:어떻게계산하나요|{_ASK})|동일(?:한)?교과목(?:의학점(?:중복)?계산(?:기준|방법)?{_PARTICLE}{_ASK}|을(?:중복해서)?수강할수있나요))",
    "course-counting.post-completion-equivalence": r"(?:이수한뒤동일대체교과목으로지정된경우학점계산은|이미이수한과목이나중에동일대체과목으로지정되면학점은)(?:어떻게하나요|어떻게세나요|" + _ASK + r")",
    "course-counting.retake": r"재수강(?:한과목의기존이수학점은어떻게처리하나요|할때학점중복계산기준을" + _ASK + r")",
    "credits.general.recognition-cap": r"교양(?:학점은최대몇학점까지인정되나요|인정학점의상한을" + _ASK + r")",
    "credits.general.remaining-allocation": rf"교양(?:{_PARTICLE}(?:잔여|남는)학점(?:은|을)?어떤이수구분으로채울수있나요|{_PARTICLE}(?:잔여|남는)학점(?:의)?배분기준{_PARTICLE}{_ASK})",
    "credits.graduation.remaining-allocation": r"(?:졸업잔여학점은어떤교과목으로채우나요|자유선택으로졸업잔여학점을채우는기준을" + _ASK + r")",
    "general.balanced-area-coverage": rf"균형교양(?:에서몇(?:개)?영역(?:을)?이수해야하나요|(?:의)?영역(?:별)?이수기준{_PARTICLE}{_ASK})",
    "general.recommended-courses": r"(?:(?:컴퓨터공학과에서)?권장하는교양과목을" + _ASK + r"|추천되는교양과목은어떤과목인가요)",
    "graduation.thesis.completion-result": rf"(?:{_ZERO_THESIS_FRAME}|졸업논문(?:은(?:영|0)학점인데이수하지않으면어떤결과인가요|미이수결과를{_ASK}))",
    "graduation.thesis.linked-program-exemption": r"학석사연계과정(?:의졸업논문|학생의논문)면제(?:기준|규정|정책)을" + _ASK,
    "graduation.thesis.required": r"(?:졸업논문을반드시이수해야하나요|졸논이필수요건인지" + _ASK + r")",
    "graduation.thesis.substitution": r"졸업논문(?:대체요건으로안내된내용을" + _ASK + r"|을대신하는요건이자료에나와있나요)",
    "major.counseling-completion": r"심층상담(?:은최소몇(?:번|회)이수해야하나요|이(?:영|0)학점이어도이수해야하나요)",
    "major.required-course-set": rf"(?:{_COURSE_SET_FRAME}|반드시들어야하는전공필수과목들을{_ASK}|전필과목은총몇개이며어떤과목인가요)",
    "operations.pccp-current-trial": _PCCP_SCORE_FRAME,
    "operations.coding-test-failure": r"(?:코딩테스트를통과하지못하면어떤조치를받나요|코테미통과시처리기준을" + _ASK + r")",
    "operations.graduation-work-prerequisite": r"졸업작품(?:을수강하기전에충족해야할조건을" + _ASK + r"|수강의선수요건은무엇인가요)",
    "credits.general.bundle": rf"교양(?:(?:이수)?학점(?:의)?기준{_PARTICLE}{_PRESENTATION}{_ASK}|(?:이수)?학점(?:의)?(?:전체)?구성{_PARTICLE}(?:{_ASK}|어떻게되나요))",
    "credits.major.bundle": rf"전공(?:(?:이수)?학점(?:의)?기준{_PARTICLE}{_PRESENTATION}{_ASK}|(?:이수)?학점(?:의)?(?:전체)?구성{_PARTICLE}(?:{_ASK}|어떻게되나요))",
}


def polite_statement(statement: str) -> str:
    """Finite suffix substitutions preserve every approved condition/number."""
    for old, new in (("하지 않는다.", "하지 않습니다."), ("해야 한다.", "해야 합니다."),
                     ("된다.", "됩니다."), ("한다.", "합니다."), ("이다.", "입니다."),
                     ("있다.", "있습니다."), ("없다.", "없습니다.")):
        statement = statement.replace(old, new)
    return statement


def approved_statement(rule: dict) -> str:
    """Include every approved outcome label, without changing its conditions."""
    statement = rule["decision"]["statement"]
    outcome = rule["decision"]["outcome"]
    items = outcome.get("items", [])
    if outcome["type"] == "coverage_requirement" and items:
        noun = "과목" if outcome.get("coverage_kind") == "course_set" else "영역"
        statement += f" 지정 {noun}은 " + ", ".join(item["label"] for item in items) + "이다."
    elif outcome["type"] == "recommendation" and items:
        if any(normalize_text(item["label"]).replace(" ", "") not in normalize_text(statement).replace(" ", "") for item in items):
            statement += " 권장 항목은 " + ", ".join(item["label"] for item in items) + "이다."
    return statement


def _extremum_compatible(engine, intent_id: str, clause: str) -> bool:
    minimum = re.search(r"최소(?=(?:몇)?학점|얼마)", clause) is not None
    maximum = re.search(r"최대(?=(?:몇)?학점|얼마)", clause) is not None
    if not minimum and not maximum:
        return True
    entry = next(item for item in engine.registry.intents["intents"] if item["intent_id"] == intent_id)
    outcomes = [engine.registry.rules[key]["decision"]["outcome"] for key in entry["rule_ids"]]
    return all((not minimum or (outcome["type"] == "credit_threshold" and outcome.get("comparator") == "at_least"))
               and (not maximum or outcome["type"] == "credit_recognition_cap") for outcome in outcomes)


def friendly_envelope(question: str) -> str:
    compact = normalize_text(question).replace(" ", "")
    return re.sub(r"^(?:(?:안녕하세요|안녕|조교님|학사조교님|혹시|궁금한데|질문이있는데|죄송하지만|먼저|그럼|그러면)){0,3}", "", compact)


def followup_kind(question: str) -> str | None:
    text = friendly_envelope(question)
    if re.fullmatch(r"(?:그|남은|부족한)과목(?:은|이)?(?:뭐야|뭐예요|무엇인가요|뭔가요)", text):
        return "course_reference"
    text = re.sub(r"^(?:그기준은|그건|그것은|그럼|그러면|그과목은|그과목이|그중에서)?", "", text)
    if re.fullmatch(r"(?:다시|다시한번)?(?:좀|조금)?(?:쉽게|자세히|간단히)?(?:설명해|정리해|알려)(?:줘|주세요|줄래요|주실수있을까요)|무슨뜻인가요|무슨말이에요", text):
        return "explain"
    if _GAP_TAIL.fullmatch(text) or re.fullmatch(r"(?:부족한|남은)학점(?:을)?" + _ASK, text):
        return "gap"
    if _CREDIT_TAIL.fullmatch(text):
        return "credits"
    if re.fullmatch(r"몇(?:과목|개과목)(?:을|이)?(?:남았어|남았나요|있나요|인가요|이야|들어야하나요|들어야해요)?|그과목(?:은|이)?뭐예요", text):
        return "courses"
    if re.fullmatch(r"몇(?:번|회)(?:을|를)?(?:인가요|이야|해야하나요|이수해야하나요|이수해야해요)?", text):
        return "count"
    return None


def canonical_question(engine, intent_ids: list[str], *, gap=False) -> str:
    entries = {entry["intent_id"]: entry for entry in engine.registry.intents["intents"]}
    if len(intent_ids) == 1 and intent_ids[0] in _CANONICAL:
        return _CANONICAL[intent_ids[0]]
    return " 및 ".join(entries[key]["aliases"][0] for key in intent_ids) + (" 부족학점" if gap else " 기준 알려주세요")


def _parse_clause(engine, clause: str):
    matches = []
    for entry in engine.registry.intents["intents"]:
        aliases = (*entry["aliases"], *_SYNONYMS.get(entry["intent_id"], ()))
        types = {engine.registry.rules[key]["decision"]["outcome"]["type"] for key in entry["rule_ids"]}
        for alias in aliases:
            if not clause.startswith(alias):
                continue
            tail = clause[len(alias):]
            gap = _GAP_TAIL.fullmatch(tail) is not None
            compatible = (gap or _CREDIT_TAIL.fullmatch(tail)) and types <= {"credit_threshold", "credit_recognition_cap"}
            completion = types <= {"boolean_requirement", "completion_requirement"} and re.fullmatch(_PARTICLE + r"(?:필수인가요|필수예요|필수야|꼭이수해야하나요|이수해야해요|이수해야하나요|몇번이수해야하나요|몇회이수해야하나요)", tail)
            if _TAIL.fullmatch(tail) or compatible or completion:
                matches.append((len(alias), entry["intent_id"], gap))
    for key, topic in _CREDIT_TOPICS.items():
        pattern = topic + r"(?:과목으로|로)?" + _PARTICLE + r"(?:최소|총|전체|이수)?(?:학점)?(?:기준)?" + _PARTICLE + _CREDIT_FRAME_END
        if re.fullmatch(pattern, clause):
            matches.append((len(clause) + 1, key, False))
    for key, pattern in _FRAMES.items():
        if re.fullmatch(pattern, clause):
            matches.append((len(clause) + 1, key, False))
    matches = [match for match in matches if _extremum_compatible(engine, match[1], clause)]
    if not matches:
        return None
    length = max(item[0] for item in matches)
    selected = {(key, gap) for size, key, gap in matches if size == length}
    return next(iter(selected)) if len(selected) == 1 else None


def _parse_topics(engine, body: str):
    single = _parse_clause(engine, body)
    if single:
        return [single]
    # Recursion is bounded by the four explicit clauses, never partial matching.
    def parse(text, remaining=4):
        single = _parse_clause(engine, text)
        if single:
            return [single]
        if remaining == 1:
            return None
        for match in re.finditer(r"그리고|및|이랑|랑|와|과", text):
            left = _parse_clause(engine, text[:match.start()])
            if left:
                right = parse(text[match.end():], remaining - 1)
                if right:
                    return [left, *right]
        return None
    return parse(body)


def resolve_question(engine, request: AcademicAnswerRequest) -> tuple[AcademicAnswerResponse, str | None]:
    """Return current engine evidence; no unknown clause is discarded."""
    routing_probe = False
    try:
        base = engine.answer(request)
    except ValueError as exc:
        if str(exc) != "unrelated earned-credit metric":
            raise
        # A complete friendly topic must be resolved before facts can be
        # associated with its metric. The final canonical replay still checks
        # every supplied metric; no value is dropped from the actual answer.
        base = engine.answer(request.model_copy(update={"earned_credits": {}}))
        routing_probe = True
    if base.status == "supported":
        # An empty-fact retry is only a routing probe: never return its evidence
        # or suppress the original structured-fact incompatibility.
        if routing_probe:
            base = engine.answer(request)
        if not all(_extremum_compatible(engine, key, normalize_text(request.question).replace(" ", "")) for key in base.intent_ids):
            return engine._unsupported(base.packet_id, base.evidence_packet.scope.model_dump(), "insufficient_evidence",
                "요청하신 최대·최소 방향을 뒷받침할 승인된 기준이 없습니다.", "missing"), None
        return base, canonical_question(engine, base.intent_ids, gap=bool(base.calculations and any(term in normalize_text(request.question) for term in ("부족", "남", "더"))))
    if (base.status == "conflict" or _question_exceeds_scope(normalize_text(request.question))
            or (request.admission_year, request.matched_curriculum_year, normalize_text(request.department)) != (2026, 2026, "컴퓨터공학과")):
        return base, None
    normalized = normalize_text(request.question)
    compact = normalized.replace(" ", "")
    config = engine.registry.intents
    if (_question_exceeds_scope(normalized) or engine._is_individual_determination(normalized, compact)
            or _asks_if_one_requirement_is_enough_for_graduation(compact)
            or any(alias in compact for group in ("exception_aliases", "individual_determination_aliases", "negation_aliases", "disjunction_aliases", "comparison_aliases", "broad_graduation_aliases") for alias in config[group])):
        return base, None
    body = friendly_envelope(request.question)
    body = re.sub(r"^(?:(?:2026|26)(?:학번|학년도|교육과정))?(?:컴퓨터공학과)?(?:의|에서)?", "", body)
    selected = _parse_topics(engine, body)
    if not selected:
        return base, None
    ids = list(dict.fromkeys(key for key, _ in selected))
    gap = any(item[1] for item in selected)
    # Exception relations and protected operations still cross the complete
    # canonical engine guard; no numeric eligibility or individual result grammar.
    resolved = canonical_question(engine, ids, gap=gap)
    candidate = engine.answer(request.model_copy(update={"question": resolved}))
    if candidate.status != "supported":
        return candidate if candidate.status == "conflict" else base, None
    return candidate, resolved


def choices(engine, ids: list[str]) -> list[dict[str, str]]:
    entries = {entry["intent_id"]: entry for entry in engine.registry.intents["intents"]}
    result = []
    for key in dict.fromkeys(ids):
        if key in entries:
            entry = entries[key]
            label = " / ".join(engine.registry.rules[rule]["label"] for rule in entry["rule_ids"])
            result.append({"label": label[:100], "question": canonical_question(engine, [key])})
            if len(result) == 3:
                break
    return result


@dataclass(frozen=True)
class FriendlyResolution:
    base: AcademicAnswerResponse
    context_used: bool = False
    context_question: str | None = None
    clarification_choices: tuple[dict, ...] = ()


def resolve_friendly(engine, request, previous: str | None) -> FriendlyResolution:
    kind = followup_kind(request.question)
    if kind is None:
        base, anchor = resolve_question(engine, request)
        candidates = []
        if base.status == "insufficient_evidence":
            entries = engine._select_intents(normalize_text(request.question).replace(" ", ""), normalize_text(request.question))
            candidates = choices(engine, [entry["intent_id"] for entry in entries] or ["credits.graduation.total", "credits.general.total", "credits.major.total"])
        return FriendlyResolution(base, context_question=anchor, clarification_choices=tuple(candidates))
    # Validate the entire current request before resolving its pronoun; replay
    # only a supported explicit anchor with current structured credits.
    original = engine.answer(request.model_copy(update={"earned_credits": {}}))
    defaults = ["credits.graduation.total", "credits.general.total", "credits.major.total"]
    def refuse(message, ids=defaults):
        scope = {"admission_year": request.admission_year, "matched_curriculum_year": request.matched_curriculum_year,
                 "department": request.department}
        base = engine._unsupported(original.packet_id, scope, "insufficient_evidence", message, "missing")
        return FriendlyResolution(base, clarification_choices=tuple(choices(engine, ids)))
    engine.validate_request(request)
    if (original.status == "conflict" or _question_exceeds_scope(normalize_text(request.question))
            or (request.admission_year, request.matched_curriculum_year, normalize_text(request.department)) != (2026, 2026, "컴퓨터공학과")):
        return FriendlyResolution(original)
    if not previous:
        return refuse("어떤 기준을 말씀하시는지 먼저 선택해 주세요.")
    prior_request = request.model_copy(update={"question": previous})
    prior, anchor = resolve_question(engine, prior_request)
    if prior.status in {"out_of_scope", "conflict"}:
        return FriendlyResolution(prior)
    if prior.status != "supported" or anchor is None:
        return refuse("이전 질문을 현재 근거로 확인할 수 없어요. 확인하려는 기준을 직접 질문해 주세요.", [])
    if kind == "explain":
        return FriendlyResolution(prior, True, anchor)
    if kind == "course_reference":
        return refuse("어떤 과목을 말씀하시는지 과목명을 적어 주세요. 기준만으로 특정 미이수 과목을 추정하지 않아요.", [])
    rules = [engine.registry.rules[item.rule_id] for item in prior.evidence_packet.applied_rules]
    if len(rules) != 1 or len(prior.intent_ids) != 1:
        rule_ids = {item.rule_id for item in prior.evidence_packet.applied_rules}
        candidates = [entry["intent_id"] for entry in engine.registry.intents["intents"] if entry["kind"] == "specific" and len(entry["rule_ids"]) == 1 and entry["rule_ids"][0] in rule_ids]
        candidates.sort(key=lambda key: (not key.endswith(".total"), key))
        return refuse("여러 기준이 있어요. 학점이나 횟수를 확인할 대상을 하나 골라 주세요.", candidates)
    outcome = rules[0]["decision"]["outcome"]
    if not _extremum_compatible(engine, prior.intent_ids[0], normalize_text(request.question).replace(" ", "")):
        return refuse("요청하신 최대·최소 방향에 해당하는 근거가 없어요. 확인할 기준을 직접 적어 주세요.", [])
    compatible = {"gap": outcome["type"] == "credit_threshold", "credits": outcome["type"] == "credit_threshold",
                  "courses": outcome["type"] == "coverage_requirement" and outcome.get("coverage_kind") == "course_set",
                  "count": outcome["type"] == "completion_requirement" and "minimum_completions" in outcome}[kind]
    if not compatible:
        return refuse("이전 기준과 질문의 학점·과목·횟수 단위가 달라요. 확인할 항목을 직접 적어 주세요.", [])
    query = canonical_question(engine, prior.intent_ids, gap=kind == "gap")
    base = engine.answer(request.model_copy(update={"question": query}))
    return FriendlyResolution(base, base.status == "supported", query if base.status == "supported" else None)


def policy_presentation(base, registry, generated: str | None = None) -> str:
    if base.status != "supported":
        return base.answer + " 확인할 항목을 구체적으로 말씀해 주시면 승인된 근거 범위에서 다시 확인할게요. 개인 예외 적용과 최종 졸업 판정은 별도 확인이 필요합니다."
    statements = []
    for item in base.evidence_packet.applied_rules:
        rule = registry.rules[item.rule_id]
        statement = approved_statement(rule)
        statements.append(polite_statement(statement))
    parts = [generated] if generated is not None else ["확인된 기준을 안내해 드릴게요.", *statements]
    for calculation in base.calculations:
        label = _LABELS[calculation.metric]
        particle = "은" if (ord(label[-1]) - 0xAC00) % 28 else "는"
        parts.append(f"입력하신 이수학점과 비교하면 {label}{particle} {calculation.earned}학점을 이수했고, 이 학점 기준의 부족분은 {calculation.gap}학점입니다.")
    parts.extend(["아래 인용에서 기준과 적용 범위를 확인해 주세요.", "개인 예외 적용이나 최종 졸업 판정은 별도 확인이 필요합니다."])
    return " ".join(parts)


def friendly_transcript_query(question: str) -> str:
    text = friendly_envelope(question)
    text = re.sub(r"몇학점(?:이)?부족한지" + _ASK + r"$", "몇학점부족해", text)
    text = re.sub(r"((?:부족한|남은)학점)(?:을)?" + _ASK + r"$", r"\1알려줘", text)
    return re.sub(_ASK + r"$", "알려줘", text)


def current_transcript_question(question: str) -> str:
    """Dedicated friendly-record prefix only; retain all other raw characters.

    This is not a policy-safety exception: the caller still validates the whole
    remaining question, and no name, identifier or inline record is removed.
    Anchors omit this generic reference and are replayed with current records.
    """
    polite = r"(?:(?:안녕하세요|조교님|학사조교님|혹시|죄송하지만)[ ,.!?]*){0,2}"
    generic = r"(?:(?:내|제|현재(?:[ \t]*(?:확인한|확인된))?|확인한|확인된)[ \t]*)?(?:성적표|이수[ \t]*(?:기록|내역))"
    relation = r"[ \t]*(?:에서|(?:를[ \t]*)?기준으로|로)[ \t]*"
    match = re.match(r"^[ \t]*" + polite + generic + relation, question)
    return question[match.end():] if match and question[match.end():].strip() else question


def transcript_presentation(response) -> str:
    if not response.selected_checks:
        return response.answer + " 현재 확인된 성적표에서 비교할 항목을 하나 적어 주세요. 최종 졸업 여부는 여기서 확정하지 않아요."
    parts = ["확인하신 현재 성적표로 해당 항목을 비교했어요."]
    for check in response.selected_checks:
        if check.result == "needs_review":
            parts.append(f"{check.label}은 기록이나 배분 근거를 더 확인해야 해서 충족 여부를 확정할 수 없어요. {check.note}")
        elif check.gap is not None:
            unit = "영역" if check.check_id == "general.balanced.area_coverage" else "학점"
            if check.result == "met":
                parts.append(f"{check.label}은 입력된 기록의 {unit} 수 비교에서는 기준을 충족해요. 부족분은 {check.gap}{unit}입니다.")
            else:
                parts.append(f"{check.label}은 입력된 기록과 비교하면 {check.gap}{unit}이 부족해요.")
        elif check.missing_courses:
            parts.append(f"{check.label}에서 아직 이수하지 않은 것으로 확인된 과목은 " + ", ".join(check.missing_courses) + "입니다.")
        else:
            parts.append(f"{check.label}은 현재 확인된 이수 기록에서 해당 기준을 충족해요.")
    for item in response.verification_items:
        parts.append(item.message + " " + item.action)
    parts.extend(["아래 항목별 근거와 확인할 기록을 함께 살펴봐 주세요.",
                  "성적표의 일부 요건을 비교한 결과이며 개인 예외 적용과 최종 졸업 판정은 별도 확인이 필요해요."])
    return " ".join(parts)
