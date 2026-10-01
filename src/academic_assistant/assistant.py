"""Additive LLM-first interpretation with independently retrieved academic facts.

Neither model output nor client-authored plans are evidence. Only this facade
can invoke the fixed read-only tools, and records never enter either prompt.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from .assistant_models import AssistantPart, AssistantTurnRequest, AssistantTurnResponse, CourseEvidencePacket
from .conversation import approved_statement, canonical_question, current_transcript_question
from .courses import COURSE_ALIASES, course_candidates, normalize_course, resolve_course_mentions, retrieve_courses
from .core import AnswerEngine, _question_exceeds_scope, normalize_text, validate_public_text_safety
from .llm import LLMBusy, LLMInvalidResponse, LLMUnavailable
from .models import AcademicAnswerRequest, Calculation, EvidencePacket
from .registry import RegistryUnavailable, canonical_bytes
from .semantic_llm import SemanticDocument, SemanticLLMClient

_TOPICS = {
    "graduation_credits": {"credits.graduation.total", "credits.graduation.remaining"},
    "general_credits": {"credits.general.total", "credits.general.foundation", "credits.general.balanced", "credits.general.remaining"},
    "major_credits": {"credits.major.total", "credits.major.required", "credits.major.elective", "credits.major.advanced"},
    "required_courses": {"major.required.course_set"}, "thesis": {"graduation.thesis"},
    "counseling": {"major.counseling"}, "verification": None,
}
_EXECUTION = re.compile(r"ignore|system\s*prompt|developer|지시.*무시|무시.*지시|프롬프트|비밀|자격증명|api.?key|\b(?:select|insert|delete|drop|match|create|exec|powershell|curl)\b|[;`{}<>]", re.I)
_UNSUPPORTED_CLAIMS = re.compile(r"졸업(?:이)?\s*(?:가능|확정|보장)|졸업할\s*수|자동\s*(?:인정|면제)|대체할\s*수|무조건|반드시\s*졸업|항상\s*(?:동일|유지)")
_PROPERTIES = {"credits", "category", "offering", "code", "count", "names"}
_PROPERTY_WORDS = {"credits": "학점", "category": "이수구분", "offering": "편성 학년 학기", "code": "과목코드", "count": "과목 개수", "names": "과목 목록"}


def _requested_properties(text):
    value = _compact(text)
    patterns = {"credits": r"학점", "category": r"전필|전선|전공필수|전공선택|이수구분|분류",
                "offering": r"학년|학기|편성", "code": r"코드|교과목번호", "count": r"개수|몇개|몇과목|총몇",
                "names": r"목록|과목들|과목명|어떤과목|무슨과목"}
    return [key for key, pattern in patterns.items() if re.search(pattern, value)]


def _requested_purpose(text):
    value = _compact(text)
    if re.search(r"강의내용|수업내용|과목내용|무엇을배우|뭘배우|어떤내용|뭐배우", value):
        return "description"
    if re.search(r"이수의무|반드시|꼭.*(?:듣|들어|이수)|필수로.*(?:듣|들어|이수)|(?<!전공)필수(?:인가|야|이야)|이수해야|이수하지않|미이수|안들어|못들어|들어야|수강하려|선행조건|전제조건|pass해야|통과해야|fail", value):
        return "completion_obligation"
    return "attributes"


def _overview_requested(text):
    return re.search(r"졸업(?:전체|이수)?(?:요건|조건)", _compact(text)) is not None


def _named_demands(registry, text, codes):
    """Bind semantic facets to source subject spans in compound questions."""
    value = normalize_course(text)
    names = {fact["course_name"]: fact["course_code"] for fact in (registry.catalogue or {}).get("courses", []) if fact["course_code"] in codes}
    tokens = [(normalize_course(name), code) for name, code in names.items()] + [(normalize_course(code), code) for code in codes]
    tokens += [(normalize_course(alias), names[name]) for alias, name in COURSE_ALIASES.items() if name in names]
    spans = []
    for token, code in sorted(tokens, key=lambda pair: len(pair[0]), reverse=True):
        for match in re.finditer(re.escape(token), value):
            if not any(match.start() < end and match.end() > start for start, end, _ in spans):
                spans.append((match.start(), match.end(), code))
    spans.sort()
    demands = {}
    for index, (start, _, code) in enumerate(spans):
        fragment = value[start:spans[index + 1][0] if index + 1 < len(spans) else len(value)]
        props, purpose = _requested_properties(fragment), _requested_purpose(fragment)
        if not props and purpose == "attributes":
            props, purpose = _requested_properties(text), _requested_purpose(text)
        demands[code] = (set(props), purpose)
    return demands


def _bare_entity(registry, text, codes):
    if not codes:
        return False
    names = [fact["course_name"] for fact in (registry.catalogue or {}).get("courses", []) if fact["course_code"] in codes]
    tokens = [*names, *codes, *(alias for alias, name in COURSE_ALIASES.items() if name in names)]
    remainder = _compact(text)
    for token in sorted(tokens, key=len, reverse=True):
        remainder = remainder.replace(_compact(token), "")
    return re.fullmatch(r"(?:그럼|그러면|그리고|및|랑|와|과|은|는|이|가|요|만)*", remainder) is not None


def _unsupported_verdict(text):
    checked = re.sub(r"졸업\s*가능\s*여부(?:나\s*학점\s*취득)?(?:를)?\s*(?:판정|확정)하지\s*않(?:는다|습니다)", "", text)
    checked = re.sub(r"자동\s*면제(?:로)?\s*(?:판정|확정)하지\s*않(?:는다|습니다)", "", checked)
    return _UNSUPPORTED_CLAIMS.search(checked) is not None


def _compact(value):
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", value).lower())


def _exceeds_scope(text, registry=None):
    if registry is not None:
        facts = (registry.catalogue or {}).get("courses", [])
        names = {fact["course_name"] for fact in facts}
        identities = names | {fact["course_code"] for fact in facts}
        identities |= {alias for alias, name in COURSE_ALIASES.items() if name in names}
        next_identity = re.compile(r"과\s*(?:" + "|".join(r"\s*".join(re.escape(char) for char in token) for token in sorted(identities, key=len, reverse=True)) + r")", re.I)
        # Source identities are courses, not departments. Do not mask an
        # identity prefix inside an explicitly named department/major, or its
        # clear department context. PII is validated before this helper runs.
        for token in sorted(identities, key=len, reverse=True):
            pattern = re.compile(r"(?<![가-힣a-z0-9])" + r"\s*".join(re.escape(char) for char in token), re.I)
            external_identity = False
            def mask(match):
                nonlocal external_identity
                tail = text[match.end():]
                head = text[:match.start()]
                # Category is a predicate of this course, whereas studying a
                # discipline names a different scope. Grammatical particles
                # do not turn one into the other.
                category = re.match(r"(?:을|를|은|는|이|가|의|도|쪽)?\s*(?:전공\s*(?:필수|선택)|전필|전선)", tail)
                conjunction = next_identity.match(tail)
                attached = (re.match(r"학과|학부", tail)
                            or token.endswith("학") and tail.startswith(("과", "부")) and not conjunction)
                context = not category and re.match(r"(?:을|를|은|는|이|가|의|도|쪽)?\s*(?:학과|학부|전공)", tail)
                prefix_context = re.search(r"(?<![가-힣])(?:전공|학과|학부)(?:은|는|이|가|명)?\s*[:：=]?\s*$", head)
                external_identity |= bool(attached or context or prefix_context)
                return match.group() if attached or context or prefix_context else " 과목 "
            text = pattern.sub(mask, text)
            if external_identity:
                return True
    scoped = re.sub(r"(학점|과목|요건|목록|학기|논문|학년|전필|전선|전공필수|전공선택|개수)과\b", r"\1와", normalize_text(text))
    return _question_exceeds_scope(scoped)


def _document(result):
    if not isinstance(result, SemanticDocument) or type(result.cached) is not bool or type(result.typed_plan) is not bool or not isinstance(result.document, dict):
        raise LLMInvalidResponse("untrusted semantic document")
    return result.document


def _plan(result, registry, previous):
    document = _document(result)
    if set(document) != {"requests", "context_used"} or type(document["context_used"]) is not bool:
        raise LLMInvalidResponse("invalid semantic plan")
    requests = document["requests"]
    if not isinstance(requests, list) or not 1 <= len(requests) <= 4 or document["context_used"] and not previous:
        raise LLMInvalidResponse("invalid semantic request count/context")
    allowed = {entry["intent_id"] for entry in registry.intents["intents"]}
    for item in requests:
        if not isinstance(item, dict):
            raise LLMInvalidResponse("invalid semantic tool")
        kind = item.get("kind")
        if type(kind) is not str:
            raise LLMInvalidResponse("invalid tool kind")
        if kind == "rule":
            ids = item.get("intent_ids")
            if set(item) != {"kind", "intent_ids"} or not isinstance(ids, list) or not 1 <= len(ids) <= 4 or any(type(key) is not str or key not in allowed for key in ids) or len(set(ids)) != len(ids):
                raise LLMInvalidResponse("unknown academic intent")
        elif kind == "courses":
            filters = item.get("filters")
            if not {"kind", "filters"} <= set(item) or set(item) - {"kind", "filters", "purpose", "properties"} or not isinstance(filters, dict) or set(filters) - {"name", "category", "year", "semester"}:
                raise LLMInvalidResponse("invalid course filters")
            if result.typed_plan and not {"purpose", "properties"} <= set(item):
                raise LLMInvalidResponse("typed course plan omitted purpose/properties")
            if result.typed_plan and item.get("purpose") == "completion_obligation" and "name" not in filters:
                raise LLMInvalidResponse("named obligation tool requires a course name")
            if "purpose" in item and (type(item["purpose"]) is not str or item["purpose"] not in {"attributes", "completion_obligation", "description"}):
                raise LLMInvalidResponse("invalid course purpose")
            if "properties" in item:
                properties = item["properties"]
                if not isinstance(properties, list) or len(properties) > 6 or any(type(key) is not str or key not in _PROPERTIES for key in properties) or len(set(properties)) != len(properties):
                    raise LLMInvalidResponse("invalid course properties")
            if "name" in filters:
                name = filters["name"]
                if type(name) is not str or not 1 <= len(name.strip()) <= 100 or _EXECUTION.search(name):
                    raise LLMInvalidResponse("invalid course name")
                try:
                    validate_public_text_safety(name)
                except ValueError as exc:
                    raise LLMInvalidResponse("provider course name is identifying") from exc
            if "category" in filters and (type(filters["category"]) is not str or filters["category"] not in {"major_required", "major_elective"}):
                raise LLMInvalidResponse("invalid category")
            for key, values in (("year", {1, 2, 3, 4}), ("semester", {1, 2})):
                if key in filters and (type(filters[key]) is not int or filters[key] not in values):
                    raise LLMInvalidResponse("invalid offering filter")
        elif kind == "transcript":
            if set(item) != {"kind", "topic"} or type(item["topic"]) is not str or item["topic"] not in _TOPICS:
                raise LLMInvalidResponse("invalid transcript topic")
        elif kind not in {"greeting", "clarify", "out_of_scope", "requirements_overview"} or set(item) != {"kind"}:
            raise LLMInvalidResponse("unknown read-only tool")
    return document


def _numbers(text):
    return set(re.findall(r"\d+", text))


_METRIC_SUBJECTS = {
    "credits.general.foundation": r"기초교양|교양기초",
    "credits.general.balanced": r"균형교양|교양균형",
    "credits.general.remaining": r"교양잔여|잔여교양",
    "credits.general.total": r"총교양|교양총(?:학점)?|전체교양|교양",
    "credits.general.recognized": r"교양(?:의)?(?:졸업학점)?인정|교양",
    "credits.major.required": r"전공필수|필수전공|전필",
    "credits.major.elective": r"전공선택|선택전공|전선",
    "credits.major.advanced": r"심화전공|전공심화|심화",
    "credits.major.minimum": r"최소전공|전공최소|기본전공|전공기본",
    "credits.major.total": r"총전공|전공총(?:학점)?|전체전공|전공",
    "credits.graduation.remaining": r"졸업잔여|잔여졸업|졸업학점잔여|잔여학점",
    "credits.graduation.total": r"졸업총(?:학점)?|총졸업|졸업",
}
_NEGATED_REQUIREMENT = re.compile(r"필수(?:가|는|이)?(?:아니|아닙|아닌|아님)|필요(?:가|는|하지)?(?:없|않)|(?:이수|충족|채우|채울|취득).{0,12}(?:않아도|안해도|불필요|하지마|하지말|해서는안|하면안)")


def _rule_mentions(registry, text):
    """Source vocabulary span coverage, not a whole-question recognizer."""
    compact = _compact(text)
    spans = []
    for entry in registry.intents["intents"]:
        for alias in entry["aliases"]:
            if entry["kind"] == "bundle" and _compact(alias) in {"교양", "전공", "교양은", "전공은"}:
                continue
            for match in re.finditer(re.escape(_compact(alias)), compact):
                spans.append((match.start(), match.end(), entry["rule_ids"]))
        for key in entry["rule_ids"]:
            outcome = registry.rules[key]["decision"]["outcome"]
            if outcome["type"] == "credit_recognition_cap":
                for match in re.finditer(r"교양.{0,15}(?:인정|반영)(?:되는|의|은|는)?(?:상한|최대)?|교양.{0,6}(?:최대|상한)", compact):
                    spans.append((match.start(), match.end(), [key]))
    selected = [span for span in spans if not any(other[0] <= span[0] and span[1] <= other[1]
                and other[1] - other[0] > span[1] - span[0] for other in spans)]
    return {key for _, _, keys in selected for key in keys}


def _uncovered_rule_subjects(registry, expected, supplied):
    outcomes = [registry.rules[key]["decision"]["outcome"] for key in supplied]
    identities = {outcome.get("requirement") or outcome.get("target_requirement") for outcome in outcomes} - {None}
    allocations = {outcome.get("source_metric") for outcome in outcomes if outcome["type"] == "allocation_policy"}
    missing = set()
    for key in expected - supplied:
        outcome = registry.rules[key]["decision"]["outcome"]
        if outcome.get("requirement") in identities:
            continue
        if outcome.get("metric") in allocations:
            continue
        # A bare balanced-education subject can ask about area coverage rather
        # than its credit threshold. The approved typed area identity supplies
        # that subject; it is not an omitted second credit request.
        if outcome.get("metric") == "credits.general.balanced" and "general.balanced.area_coverage" in identities:
            continue
        missing.add(key)
    return missing


def _policy_course_coverage(registry, facts):
    covered = set()
    for fact in facts:
        outcome = fact["outcome"]
        if outcome.get("coverage_kind") == "course_set":
            covered.update(item["item_id"] for item in outcome.get("items", []))
        identity = outcome.get("requirement") or outcome.get("target_requirement")
        subject = {"graduation.thesis.required": "졸업논문", "major.counseling.required": "심층상담"}.get(identity)
        if subject:
            covered.update(resolve_course_mentions(registry, subject))
    return covered


def _clauses(text):
    return [value for value in re.split(r"[.!?\n,]|이며|이고|지만", text) if value.strip()]


def _verify_credit_bindings(text, facts):
    expected = {}
    for fact in facts:
        outcome = fact["outcome"]
        if outcome["type"] in {"credit_threshold", "credit_recognition_cap"}:
            value = outcome.get("credits", outcome.get("maximum_recognized_credits"))
            expected[(outcome["metric"], value)] = outcome["type"]
    if not expected:
        return
    seen = set()
    for raw in _clauses(text):
        clause = _compact(raw)
        values = list(re.finditer(r"(\d+)학점", clause))
        subjects = [(match.start(), match.end(), metric) for metric, pattern in _METRIC_SUBJECTS.items()
                    for match in re.finditer(pattern, clause)]
        subjects = [item for item in subjects if not any(other[0] <= item[0] and item[1] <= other[1]
                    and other[1] - other[0] > item[1] - item[0] for other in subjects)]
        for match in values:
            prior = [item for item in subjects if item[1] <= match.start()]
            candidates = [item for item in prior if item[1] == max((other[1] for other in prior), default=-1)]
            if not candidates:
                after = [item for item in subjects if item[0] >= match.end()]
                candidates = [item for item in after if item[0] == min((other[0] for other in after), default=-1)]
            pairs = {(item[2], int(match[1])) for item in candidates} & set(expected)
            cap = ("credits.general.recognized", int(match[1]))
            if cap in expected and "교양" in clause and re.search(r"인정|반영", clause):
                pairs.add(cap)
            if not pairs or _NEGATED_REQUIREMENT.search(clause):
                raise LLMInvalidResponse("writer changed credit subject/value/polarity")
            for pair in pairs:
                minimum = expected[pair] == "credit_threshold"
                if minimum and (not re.search(r"이상|최소|적어도", clause) or re.search(r"최대|이하|상한", clause)):
                    raise LLMInvalidResponse("writer reversed a minimum predicate")
                if not minimum and (not re.search(r"최대|상한|까지만|이내", clause) or re.search(r"최소|이상|적어도", clause)):
                    raise LLMInvalidResponse("writer reversed a maximum predicate")
                seen.add(pair)
    if seen != set(expected):
        raise LLMInvalidResponse("writer did not bind every credit fact")


def _verify_rule_text(text, facts):
    """Validate typed semantics/coverage, not membership in sentence templates."""
    compact = _compact(text)
    source = " ".join(fact["statement"] for fact in facts)
    source_compact = _compact(source)
    for subject in re.findall(r"(?<![가-힣A-Za-z])([가-힣A-Za-z0-9·]+?)(?:은|는|도)(?=\s*[^.!?\n]{0,65}(?:학점|필수|면제|인정|수강))", text):
        if (_compact(subject) not in source_compact and _compact(subject) not in
                {"기준", "이기준", "요건", "그요건", "해당기준", "수치", "이수치", "조건", "과목", "기이수과목",
                 "총학점", "이수학점", "기준학점"}):
            raise LLMInvalidResponse("writer introduced an unvalidated rule subject")
    if _numbers(text) - _numbers(source):
        raise LLMInvalidResponse("writer invented a numerical fact")
    if set(re.findall(r"\d{4}-\d{2}-\d{2}", text)) - set(re.findall(r"\d{4}-\d{2}-\d{2}", source)):
        raise LLMInvalidResponse("writer changed confirmation date")
    _verify_credit_bindings(text, facts)
    for fact in facts:
        outcome = fact["outcome"]
        kind = outcome["type"]
        if kind in {"credit_threshold", "credit_recognition_cap"}:
            if kind == "credit_threshold" and _NEGATED_REQUIREMENT.search(compact):
                raise LLMInvalidResponse("writer added a prohibited minimum action")
            credits = outcome["credits"] if kind == "credit_threshold" else outcome["maximum_recognized_credits"]
            if not re.search(rf"(?<!\d){credits}\s*학점", text):
                raise LLMInvalidResponse("writer lost credit value/unit")
            minimum = kind == "credit_threshold" and outcome.get("comparator") == "at_least"
            if minimum and not re.search(r"이상|최소|적어도", text):
                raise LLMInvalidResponse("writer reversed minimum")
            if kind == "credit_recognition_cap" and not re.search(r"최대|상한|까지만|이내", text):
                raise LLMInvalidResponse("writer reversed maximum")
        elif kind == "coverage_requirement":
            if any(_compact(item["label"]) not in compact for item in outcome.get("items", [])):
                raise LLMInvalidResponse("writer lost approved coverage labels")
            # Validate the affirmative minimum predicate separately from the
            # legitimate negative no-double-count/null-fixed-credit caveats.
            for clause in _clauses(text):
                value = _compact(clause)
                if outcome.get("fixed_credits_per_course", "absent") is None:
                    value = re.sub(r"(?:반드시|꼭)?\d+학점(?:일|이어야할)필요(?:가|는)?없", "", value)
                if _NEGATED_REQUIREMENT.search(value):
                    raise LLMInvalidResponse("writer negated required coverage")
                if outcome.get("fixed_credits_per_course", "absent") is None and re.search(r"\d+학점", value):
                    raise LLMInvalidResponse("writer invented universal coverage credits")
            if re.search(r"\d+\s*(?:개|과목|영역)[^.!?\n]{0,8}(?:미만|이하)", text):
                raise LLMInvalidResponse("writer reversed minimum coverage")
            count = outcome["minimum_items"]
            if not re.search(rf"(?<!\d){count}\s*(?:개|가지|영역|과목)", text):
                raise LLMInvalidResponse("writer dropped minimum coverage cardinality")
            if not re.search(r"이수해야|이수.*필수|반드시.*이수|모두.*이수|전부.*이수", text):
                raise LLMInvalidResponse("writer dropped affirmative required coverage")
            if outcome.get("coverage_kind") == "area_set":
                minimum = outcome["minimum_courses_per_item"]
                if not re.search(rf"(?:각각|각\s*영역|영역별)[^.!?\n]{{0,35}}(?:최소\s*{minimum}|{minimum}\s*과목\s*이상)", text) or re.search(r"\d+\s*과목\s*(?:미만|이하)", text):
                    raise LLMInvalidResponse("writer reversed per-area coverage")
            if outcome.get("one_course_may_cover_multiple_items") is False:
                if not re.search(r"각각|각\s*영역|영역별", text) or not re.search(r"동시|중복|복수|둘\s*이상", text) or not re.search(r"없|않|불가", text):
                    raise LLMInvalidResponse("writer lost distinct-area caveat")
        elif kind == "operational_policy":
            # Operational sentences contain safety-critical conditional claims.
            groups = {
                "pccp_current_trial": [r"400\s*점\s*이상", r"현행|현재", r"시범|잠정", r"향후|미래|시행", r"보장.*않|확정.*않|바뀔|변경", r"개인", r"판정.*않|확정.*않"],
                "coding_test_failure": [r"(?:캡스톤|캡디).*?(?:I|1).*?U", r"다음\s*(?:연도|해)", r"(?:II|2)", r"수강.*(?:제한|불가|없|못)", r"(?:I|1).*?(?:수강|금지).*?(?:않|아니)"],
                "graduation_work_prerequisite": [r"졸업작품", r"(?:II|2).*?(?:PASS|패스|통과)", r"수강", r"졸업.*(?:판정|확정).*않", r"학점.*(?:판정|확정).*않"]}
            if any(re.search(pattern, text, re.I) is None for pattern in groups.get(outcome.get("kind"), [])):
                raise LLMInvalidResponse("writer lost operational caveat")
        elif kind in {"boolean_requirement", "completion_requirement"}:
            if _NEGATED_REQUIREMENT.search(_compact(text)):
                raise LLMInvalidResponse("writer negated a required completion")
            if not re.search(r"필수|반드시|이수해야|꼭|이수.*필요|충족해야", text):
                raise LLMInvalidResponse("writer lost completion requirement")
        elif kind == "course_counting_policy":
            if not re.search(r"중복|두\s*번", text) or not re.search(r"않|없|불가", text):
                raise LLMInvalidResponse("writer changed counting policy")
            if outcome.get("action") == "delete_prior_completion" and not re.search(r"삭제|제외|취소", text):
                raise LLMInvalidResponse("writer dropped prior-completion action")
        elif kind == "cohort_assignment_policy":
            if not re.search(r"최초\s*입학|원래\s*입학", text) or not re.search(r"전과.*(?:아니|않|관계없)", text):
                raise LLMInvalidResponse("writer changed cohort basis")
        elif kind == "allocation_policy":
            if any(_compact(label) not in compact for label in outcome["allowed_allocations"]):
                raise LLMInvalidResponse("writer dropped allowed allocation")
        elif kind == "exception_eligibility":
            if not re.search(r"학.?석사\s*연계", text) or not re.search(r"면제.*가능", text) or not re.search(r"자동.*(?:않|아니|없)", text):
                raise LLMInvalidResponse("writer changed exemption eligibility")
        elif kind == "recommendation":
            if not re.search(r"권장|추천", text) or re.search(r"필수|반드시|무조건", text):
                raise LLMInvalidResponse("writer changed recommendation to requirement")
        # Source absence is not blanket prohibition or personal eligibility.
        if outcome.get("listing_status") == "none_listed":
            if not re.search(r"자료|명시|근거|안내|규정집|기재", text) or not re.search(r"없|않|확인.*못", text) or re.search(r"절대|금지|불가능", text):
                raise LLMInvalidResponse("writer changed evidence absence to prohibition")
        if "0학점" in fact["statement"] and "0학점" not in compact:
            raise LLMInvalidResponse("writer dropped zero-credit caveat")
        if "FAIL" in fact["statement"].upper() and "fail" not in compact:
            raise LLMInvalidResponse("writer dropped completion result")


def _verify_course_text(text, spec):
    facts = spec.get("source_facts", spec["facts"])
    properties = set(spec.get("properties", _PROPERTIES - {"code"}))
    normalized = _compact(text)
    scope_filters = (spec.get("course_summary") or {}).get("filters", {}) if not spec["named"] else {}
    scope_text = text
    for field, unit in (("year", "학년"), ("semester", "학기")):
        if field in scope_filters:
            scope_text = re.sub(rf"(?<!\d){scope_filters[field]}\s*{unit}", " 요청범위 ", scope_text)
    if spec.get("properties") is not None and re.search(r"반드시|의무|이수해야|면제|대체|출석", text):
        raise LLMInvalidResponse("writer inferred obligation from a course attribute")
    if re.search(r"개설(?:됩니다|해요|예정|보장)|수강(?:가능|할수)|항상|매년", normalized):
        raise LLMInvalidResponse("writer guaranteed an actual course offering")
    if "credits" not in properties and re.search(r"\d+\s*학점", text):
        raise LLMInvalidResponse("writer added unrequested course credits")
    if "offering" not in properties and re.search(r"\d+\s*(?:학년|학기)|(?:첫|둘째|계절)\s*학기", scope_text):
        raise LLMInvalidResponse("writer added unrequested offering")
    if "offering" not in properties and re.search(r"모두|모든|각각|전부|공통|오직|만\s*편성", text) and re.search(r"\d+\s*(?:학년|학기)", text):
        raise LLMInvalidResponse("writer converted a filter into a universal offering")
    scoped_category = not spec["named"] and bool((spec.get("course_summary") or {}).get("filters", {}).get("category"))
    if "category" not in properties and not scoped_category and re.search(r"전공\s*(?:필수|선택)|전필|전선", text):
        raise LLMInvalidResponse("writer added unrequested classification")
    # A false-premise correction is legitimate only when its two category
    # predicates are bound to the exact retrieved named subject.
    checked = normalized
    if spec.get("properties") is not None and spec["named"] and "category" in properties:
        for fact in facts:
            actual = "전공필수|전필" if fact["category"] == "major_required" else "전공선택|전선"
            wrong = "전공선택|전선" if fact["category"] == "major_required" else "전공필수|전필"
            checked = re.sub(re.escape(_compact(fact["course_name"])) + r"(?:은|는)?(?:" + wrong + r")(?:이|가)?아니라(?:" + actual + ")",
                             _compact(fact["course_name"]) + actual.split("|")[0], checked)
    if re.search(r"(?:전공필수|전필|전공선택|전선).{0,8}(?:아니|아닙|아닌|아님|않)", checked):
        raise LLMInvalidResponse("writer negated course classification")
    categories = {fact["category"] for fact in facts}
    if len(categories) == 1:
        opposite = r"전공필수|전필" if "major_elective" in categories else r"전공선택|전선"
        if re.search(opposite, checked.replace("부전공필수", "")):
            raise LLMInvalidResponse("writer changed list classification")
    # Every new factual subject must be a retrieved course or a generic
    # reference to the current packet; unrelated courses cannot piggyback.
    masked = text
    for fact in sorted(facts, key=lambda fact: len(fact["course_name"]), reverse=True):
        masked = re.sub(r"\s*".join(re.escape(char) for char in fact["course_name"]), " 해당과목", masked)
    generic = {"해당과목", "이과목", "과목", "과목들", "이과목들", "해당과목들", "목록", "이목록", "수업", "학점", "학기", "학년", "편성학년과학기", "전공필수", "전공선택", "전필", "전선"}
    for subject in re.findall(r"(?:^|[.!?\n])\s*([^:：.!?\n]{1,100})[:：](?=[^.!?\n]*(?:학점|전공필수|전공선택|학년|학기))", masked):
        if _compact(subject) not in generic:
            raise LLMInvalidResponse("writer introduced a colon factual subject")
    for subject in re.findall(r"(?<![가-힣])([가-힣]+?)(?:은|는|도)(?=[^.!?\n]{0,45}(?:\d+\s*학점|전공필수|전공선택))", masked):
        if subject not in generic:
            raise LLMInvalidResponse("writer introduced an unvalidated factual subject")
    allowed_numbers = {str(len(facts))} if properties & {"count", "names"} else set()
    allowed_numbers |= {str(scope_filters[key]) for key in ("year", "semester") if key in scope_filters}
    for fact in facts:
        allowed_numbers |= _numbers(fact["course_name"])
        if "credits" in properties:
            allowed_numbers.add(str(fact["credits"]))
        if "offering" in properties:
            allowed_numbers |= {*map(str, fact["offering_years"]), *(semester for semester in fact["offering_semesters"] if semester != "S")}
        if "code" in properties and "code" in fact:
            allowed_numbers |= _numbers(fact["code"])
    if _numbers(text) - allowed_numbers:
        raise LLMInvalidResponse("writer invented course numbers")
    for clause in _clauses(text):
        values = re.findall(r"(\d+)\s*학점", clause)
        if not values:
            continue
        if re.search(r"학점.{0,18}(?:아니|아닙|아님|않|없)", clause):
            raise LLMInvalidResponse("writer negated source course credits")
        universal = re.search(r"모든|모두|각각|각\s*과목|전부|일괄|동일|공통", clause)
        selected = [fact for fact in facts if _compact(fact["course_name"]) in _compact(clause)]
        if universal or not selected:
            selected = facts
        if len(values) == 1 and any(fact["credits"] != int(values[0]) for fact in selected):
            raise LLMInvalidResponse("writer changed a course-list credit predicate")
    if spec["named"]:
        for fact in facts:
            name = re.escape(fact["course_name"])
            if "credits" in properties and not re.search(name + rf"[^.!?\n]{{0,100}}(?<!\d){fact['credits']}\s*학점", text):
                raise LLMInvalidResponse("writer changed course-credit association")
            label = "전공\\s*필수|필수\\s*전공|전필" if fact["category"] == "major_required" else "전공\\s*선택|선택\\s*전공|전선"
            if "category" in properties and not re.search(name + r"[^.!?\n]{0,120}(?:" + label + ")", text):
                raise LLMInvalidResponse("writer changed course category")
            if "code" in properties and fact.get("code") not in text:
                raise LLMInvalidResponse("writer changed course code")
            if "offering" in properties and spec.get("properties") is not None:
                selected = " ".join(clause for clause in _clauses(text) if _compact(fact["course_name"]) in _compact(clause))
                selected = re.sub(r"(?:첫(?:번째)?|일)\s*학기", "1학기", selected)
                selected = re.sub(r"(?:두\s*번째|둘째|이)\s*학기", "2학기", selected)
                years = set(map(int, re.findall(r"(\d+)\s*학년", selected)))
                semesters = set(re.findall(r"([12])\s*학기", selected))
                if re.search(r"전\s*학년|모든\s*학년", selected):
                    years = {1, 2, 3, 4}
                if re.search(r"두\s*학기|양\s*학기|[12]\s*[·,과와/]\s*[12]\s*학기", selected):
                    semesters = {"1", "2"}
                if re.search(r"하계|동계|계절", selected):
                    semesters.add("S")
                if _compact(fact["offering_label"]) not in _compact(selected) and (years != set(fact["offering_years"]) or semesters != set(fact["offering_semesters"])):
                    raise LLMInvalidResponse("writer dropped requested source offering")
            if "부전공" in text and not fact["minor_required"]:
                raise LLMInvalidResponse("writer invented minor-required designation")
            for value in re.findall(r"(\d+)\s*학년", text):
                if int(value) not in (fact["offering_years"] or [fact["year"]]):
                    raise LLMInvalidResponse("writer changed offering year")
            semester_text = re.sub(r"(?:첫(?:번째)?|일)\s*학기", "1학기", text)
            semester_text = re.sub(r"(?:두\s*번째|둘째|이)\s*학기", "2학기", semester_text)
            for value in re.findall(r"(\d+)\s*학기", semester_text):
                if value not in (fact["offering_semesters"] or [str(fact["semester"])]):
                    raise LLMInvalidResponse("writer changed offering semester")
    elif properties & {"count", "names"} and not re.search(rf"(?<!\d){len(facts)}\s*(?:개|과목)", text):
        raise LLMInvalidResponse("writer lost derived course count")
    if not spec["named"] and re.search(r"\d+\s*(?:개|과목).{0,12}(?:아니|아닙|아님|않|없)", text):
        raise LLMInvalidResponse("writer negated derived course count")
    # Any named catalogue course must belong to this retrieved packet.
    compact = _compact(text)
    for fact in sorted(facts, key=lambda fact: len(fact["course_name"]), reverse=True):
        compact = compact.replace(_compact(fact["course_name"]), "")
    for name in spec["other_names"]:
        if _compact(name) in compact:
            raise LLMInvalidResponse("writer invented an unretrieved course")


def _write(result, specs):
    document = _document(result)
    if set(document) != {"sections"} or not isinstance(document["sections"], list) or len(document["sections"]) != len(specs):
        raise LLMInvalidResponse("writer sections outside coverage")
    texts = []
    for section, spec in zip(document["sections"], specs, strict=True):
        if not isinstance(section, dict) or set(section) != {"part_id", "text", "fact_ids"} or section["part_id"] != spec["part_id"] or section["fact_ids"] != [fact["fact_id"] for fact in spec["facts"]]:
            raise LLMInvalidResponse("writer citation coverage changed")
        text = section["text"]
        if type(text) is not str or not 1 <= len(text.strip()) <= 16000 or _EXECUTION.search(text) or _unsupported_verdict(text):
            raise LLMInvalidResponse("writer text outside safety boundary")
        source = " ".join(fact.get("statement", fact.get("course_name", "")) for fact in spec["facts"])
        if any(token in text and token not in source for token in ("장학금", "기숙사", "등록금", "제출기한", "출석", "수업시간", "졸업보장", "비밀번호")):
            raise LLMInvalidResponse("writer introduced an unsupported policy")
        try:
            validate_public_text_safety(text)
        except ValueError as exc:
            raise LLMInvalidResponse("writer introduced identifying text") from exc
        if spec["family"] == "courses":
            _verify_course_text(text, spec)
        elif spec["family"] == "obligation":
            if _NEGATED_REQUIREMENT.search(_compact(text)) or not re.search(r"필수|반드시|이수해야|이수.*필요", text):
                raise LLMInvalidResponse("writer changed approved obligation")
            if any(_compact(name) not in _compact(text) for name in spec["subjects"]) or _numbers(text):
                raise LLMInvalidResponse("writer changed obligation subject")
            if re.search(r"학점|학년|학기|전공선택|면제.*(?:가능|됩니다)|대체.*(?:가능|됩니다)", text):
                raise LLMInvalidResponse("writer added an unapproved obligation qualifier")
            masked = text
            for name in sorted(spec["subjects"], key=len, reverse=True):
                masked = masked.replace(name, "해당과목")
            for subject in re.findall(r"(?<![가-힣])([가-힣]+?)(?:은|는|도)(?=[^.!?\n]{0,45}(?:필수|반드시|이수해야|이수.*필요))", masked):
                if subject not in {"해당과목", "과목", "이과목", "해당과목들", "이과목들"}:
                    raise LLMInvalidResponse("writer introduced an unapproved obligation subject")
        else:
            _verify_rule_text(text, spec["facts"])
        texts.append(text.strip())
    return texts


class SemanticAssistant:
    def __init__(self, engine=None, llm=None):
        self.engine = engine or AnswerEngine()
        self.llm = llm if llm is not None else SemanticLLMClient.from_env()

    def _unsupported(self, title, text, status="insufficient_evidence"):
        return AssistantPart(title=title, text=text, status=status)

    def _course_clarification(self, request, candidates, properties, context_used=False):
        names = [item["course_name"] for item in candidates]
        text = (" / ".join(names) + " 중 어떤 과목을 말씀하시나요?") if len(names) > 1 else ((names[0] + " 과목을 말씀하시나요?") if names else "어떤 과목을 말씀하시나요?")
        focus = "과목 후보 " + " 또는 ".join(f"{index} {name}" for index, name in enumerate(names, 1))
        focus += " 확인 요청 " + " ".join(_PROPERTY_WORDS[prop] for prop in properties)
        return self._response(request, [self._unsupported("과목 확인", text)], "fallback", "not_applicable", context_used,
                              "ambiguous", "clarification", context_question=focus.strip()[:500])

    def _overview(self, request, index):
        groups = [
            ["credits.graduation.total", "credits.general.total", "credits.major.total", "credits.graduation.remaining", "credits.graduation.remaining-allocation"],
            ["credits.general.bundle", "credits.general.recognition-cap", "credits.general.remaining-allocation", "general.balanced-area-coverage"],
            ["credits.major.bundle", "major.required-course-set"],
            ["graduation.thesis.required", "graduation.thesis.completion-result"],
            ["major.counseling-completion"],
            ["operations.pccp-current-trial", "operations.coding-test-failure", "operations.graduation-work-prerequisite"],
        ]
        parts, specs, metrics = [], [], set()
        for ids in groups:
            # Separate canonical calls avoid the legacy whole-question parser
            # mistaking a bundle for a specific nested requirement.
            collected, original = [], []
            for intent in ids:
                part, spec, used = self._rules(request, [intent], index + len(parts))
                metrics |= used
                collected.append(part); original.extend(spec["facts"])
            if any(part.status != "supported" for part in collected):
                parts.append(next(part for part in collected if part.status != "supported"))
                continue
            packets = [part.evidence_packet for part in collected]
            applied = list({item.rule_id: item for packet in packets for item in packet.applied_rules}.values())
            citations = list({(item.rule_id, item.source_id, item.locator): item for packet in packets for item in packet.evidence}.values())
            packet = packets[0].model_copy(update={"packet_id": packets[0].packet_id + "-overview", "applied_rules": applied, "evidence": citations})
            unique = list({fact["fact_id"]: fact for fact in original}.values())
            # Model writes a short primary explanation; all nested approved
            # qualifiers remain deterministic source-linked local statements.
            primary = [fact for fact in unique if fact["outcome"]["type"] == "credit_threshold"] if len(parts) == 0 else unique[:1]
            primary_ids = {fact["fact_id"] for fact in primary}
            local = " ".join(fact["statement"] for fact in unique if fact["fact_id"] not in primary_ids)
            calculations = [calc for part in collected for calc in part.calculations]
            local += " " + " ".join(f"입력한 {calc.earned}학점과 비교하면 {calc.gap}학점이 부족합니다." for calc in calculations)
            parts.append(AssistantPart(title="졸업요건 / " + collected[0].title[:150], text=" ".join(fact["statement"] for fact in unique),
                                       status="supported", evidence_packet=EvidencePacket.model_validate(packet.model_dump()), calculations=calculations))
            if len(parts) == 1:
                # One natural primary summary fits the existing output cap.
                # Other approved conditions remain complete source statements,
                # not an LLM truncation or re-approval of academic facts.
                specs.append({"part_id": f"p{index + len(parts) - 1}", "title": parts[-1].title, "facts": primary,
                              "family": "rules", "local": local.strip(), "index": index + len(parts) - 2})
            elif calculations:
                parts[-1] = parts[-1].model_copy(update={"text": parts[-1].text + " " + " ".join(f"입력한 {calc.earned}학점과 비교하면 {calc.gap}학점이 부족합니다." for calc in calculations)})
        return parts, specs, metrics

    def _obligation(self, request, filters, index):
        packet = retrieve_courses(self.engine, {"name": filters["name"]})
        if packet.status != "supported":
            return [self._unsupported("이수 의무", "과목을 확인할 근거가 부족합니다.")], []
        required_entry = next(entry for entry in self.engine.registry.intents["intents"] if entry["intent_id"] == "major.required-course-set")
        rule = self.engine.registry.rules[required_entry["rule_ids"][0]]
        required = {item["item_id"] for item in rule["decision"]["outcome"]["items"]}
        names = [fact.course_name for fact in packet.courses]
        if all(fact.course_code in required for fact in packet.courses):
            part, spec, _ = self._rules(request, ["major.required-course-set"], index)
            if part.status != "supported":
                return [part], []
            statement = ", ".join(names) + "는 승인된 지정 이수과목이므로 반드시 이수해야 합니다. 개인 면제·대체 판정은 아닙니다."
            spec.update({"family": "obligation", "subjects": names, "local": "",
                         "facts": [{"fact_id": rule["rule_id"], "statement": statement}]})
            parts, specs = [part.model_copy(update={"title": "과목 이수 의무", "text": statement})], [spec]
            extra = []
            if any(fact.course_code == "CDA0088" for fact in packet.courses):
                extra.append("major.counseling-completion")
            if any(fact.course_code == "CDA0034" for fact in packet.courses):
                extra.append("graduation.thesis.completion-result")
            for intent in extra:
                item, extra_spec, _ = self._rules(request, [intent], index + len(parts))
                parts.append(item)
                if item.status == "supported":
                    specs.append(extra_spec)
            return parts, specs
        parts, specs = [], []
        operational = []
        if re.search(r"졸업작품", request.question) and re.search(r"수강|등록|신청|선행|조건", request.question):
            return_parts, return_spec, _ = self._rules(request, ["operations.graduation-work-prerequisite"], index)
            return [return_parts], [return_spec] if return_parts.status == "supported" else []
        if any("캡스톤" in fact.course_name for fact in packet.courses):
            operational.append("operations.coding-test-failure")
        if any("졸업작품" in fact.course_name for fact in packet.courses):
            operational.append("operations.graduation-work-prerequisite")
        for intent in operational:
            part, spec, _ = self._rules(request, [intent], index + len(parts)); parts.append(part)
            if part.status == "supported":
                specs.append(spec)
        parts.append(self._unsupported("이수 의무 확인", ", ".join(names) + "의 이수구분만으로 졸업을 위한 의무 또는 개인 면제를 확정할 수 없습니다. 적용할 승인된 요건의 추가 확인이 필요합니다."))
        return parts, specs

    def _response(self, request, parts, plan_status, generation_status="fallback", context_used=False, reason=None, kind="academic", context_question=None):
        status = next((value for value in ("conflict", "out_of_scope", "insufficient_evidence") if any(part.status == value for part in parts)), "supported")
        if not parts:
            status = "out_of_scope" if kind in {"greeting", "refusal"} else "insufficient_evidence"
        answer = "\n\n".join(part.text for part in parts) or ("안녕하세요! 2026학번 컴퓨터공학과의 과목과 이수 기준을 함께 확인해 드릴게요." if kind == "greeting" else "어떤 과목이나 이수 기준을 확인할까요? 과목명 또는 확인할 항목을 알려 주세요.")
        if len(answer) > 32000:
            raise ValueError("semantic response outside bounds")
        packet_id = "assistant-" + hashlib.sha256(canonical_bytes({"request": request.model_dump(), "registry": self.engine.registry.digest})).hexdigest()[:32]
        return AssistantTurnResponse(packet_id=packet_id, status=status, kind=kind, answer=answer,
            plan_status=plan_status, generation_status=generation_status, parts=parts,
            context_question=(context_question or request.question) if status == "supported" or kind == "clarification" and reason == "ambiguous" else None, context_used=context_used, reason_code=reason)

    def _public_catalog(self):
        registry = self.engine.registry
        return {
            "course_aliases": dict(COURSE_ALIASES),
            "intents": [{"intent_id": entry["intent_id"], "description": "; ".join(registry.rules[key]["label"] for key in entry["rule_ids"])} for entry in registry.intents["intents"]],
            "courses": [{"code": raw["course_code"], **{key: raw.get(key) for key in ("course_name", "category", "offering_years", "offering_semesters")}} for raw in (getattr(registry, "catalogue", None) or {}).get("courses", [])]}

    def _rules(self, request, ids, index):
        registry = self.engine.registry
        entries = {entry["intent_id"]: entry for entry in registry.intents["intents"]}
        keys = list(dict.fromkeys(key for intent in ids for key in entries[intent]["rule_ids"]))
        metrics = {registry.rules[key]["decision"]["outcome"].get("metric") for key in keys} - {None}
        facts = {metric: value for metric, value in request.earned_credits.items() if metric in metrics}
        result = self.engine.answer(AcademicAnswerRequest(question=canonical_question(self.engine, ids),
            admission_year=request.admission_year, matched_curriculum_year=request.matched_curriculum_year,
            department=request.department, earned_credits=facts))
        packet = EvidencePacket.model_validate(result.evidence_packet.model_dump())
        local = " ".join(f"입력한 {calc.earned}학점과 비교하면 {calc.gap}학점이 부족합니다." for calc in result.calculations)
        public_facts = [{"fact_id": item.rule_id, "statement": approved_statement(registry.rules[item.rule_id]), "outcome": registry.rules[item.rule_id]["decision"]["outcome"]} for item in packet.applied_rules] if packet.status == "supported" else []
        fallback = " ".join(fact["statement"] for fact in public_facts) or result.answer
        part = AssistantPart(title=" / ".join(registry.rules[key]["label"] for key in keys)[:200], text=(fallback + " " + local).strip(),
            status=packet.status, evidence_packet=packet, calculations=result.calculations)
        spec = {"part_id": f"p{index}", "title": part.title, "facts": public_facts, "family": "rules", "local": local, "index": index - 1}
        return part, spec, metrics

    def _courses(self, filters, index, properties=None):
        packet = CourseEvidencePacket.model_validate(retrieve_courses(self.engine, filters).model_dump())
        if packet.status != "supported":
            return AssistantPart(title="과목 조회", text=" ".join(issue.message for issue in packet.issues), status=packet.status, course_evidence=packet), None
        rows = []
        for fact in packet.courses:
            category = "전공필수" if fact.category == "major_required" else "전공선택"
            offering = fact.offering_label or (f"{fact.year}학년 {fact.semester}학기" if fact.year is not None and fact.semester is not None else "편성 학년·학기 미확인")
            minor = ", 원문 부전공필수 표시" if getattr(fact, "minor_required", False) else ""
            rows.append(f"{fact.course_name} ({fact.course_code}): {fact.credits}학점, {category}, {offering}{minor}")
        local = "\n".join(rows)
        part = AssistantPart(title="과목 조회", text=f"검증된 목록에서 {len(rows)}과목을 확인했습니다.\n{local}", status="supported", course_evidence=packet)
        retrieved = {fact.course_name for fact in packet.courses}
        other = [raw["course_name"] for raw in (getattr(self.engine.registry, "catalogue", None) or {}).get("courses", []) if raw["course_name"] not in retrieved]
        facts = [{"fact_id": fact.course_id, "code": fact.course_code, "course_name": fact.course_name, "credits": fact.credits,
                  "category": fact.category, "year": fact.year, "semester": fact.semester,
                  "offering_years": fact.offering_years, "offering_semesters": fact.offering_semesters,
                  "offering_label": fact.offering_label, "minor_required": getattr(fact, "minor_required", False)} for fact in packet.courses]
        summary = {"course_count": len(facts), "categories": sorted({fact["category"] for fact in facts}),
                   "filters": dict(filters)}
        spec = {"part_id": f"p{index}", "title": part.title, "facts": facts, "family": "courses", "course_summary": None if "name" in filters else summary,
                      "named": "name" in filters, "other_names": other, "local": local, "index": index - 1}
        if properties is not None:
            spec["source_facts"] = facts
            spec["properties"] = properties
            columns = {"credits": {"credits"}, "category": {"category"}, "offering": {"offering_years", "offering_semesters", "offering_label", "year", "semester"}, "code": {"code"}}
            keys = {"fact_id", "course_name"} | {column for prop in properties for column in columns.get(prop, set())}
            spec["facts"] = [{key: fact[key] for key in keys} for fact in facts]
            spec["local"] = ", ".join(fact["course_name"] for fact in facts) if "names" in properties and "name" not in filters else ""
            rendered = []
            for fact in facts:
                values = ([f"{fact['credits']}학점"] if "credits" in properties else [])
                values += [("전공필수" if fact["category"] == "major_required" else "전공선택")] if "category" in properties else []
                values += [fact["offering_label"]] if "offering" in properties else []
                values += [fact["code"]] if "code" in properties else []
                if values:
                    rendered.append(fact["course_name"] + ": " + ", ".join(values))
            fallback = "\n".join(rendered) or (f"해당 목록은 {len(facts)}과목입니다." + (" " + spec["local"] if spec["local"] else ""))
            part = part.model_copy(update={"text": fallback})
            if spec["course_summary"] is not None:
                spec["course_summary"] = {"course_count": len(facts), "filters": dict(filters)}
                if "category" in properties:
                    spec["course_summary"]["categories"] = summary["categories"]
        else:
            # Legacy mock plans still validate every fact, but no canonical
            # all-field row is appended to actual generated prose.
            spec["local"] = ", ".join(fact["course_name"] for fact in facts) if "name" not in filters else ""
        return part, spec

    def _transcript(self, request, topic, assessment):
        from .transcript_assessment import TranscriptAssessor
        if request.transcript is None:
            return [self._unsupported("현재 기록 비교", "현재 확인한 이수 기록이 필요합니다. 기록을 확인한 뒤 비교할 수 있습니다.")], assessment
        if assessment is None:
            assessment = TranscriptAssessor(self.engine).assess(request.transcript)
        if not assessment.checks:
            return [self._unsupported("현재 기록 비교", assessment.answer, assessment.status)], assessment
        keys = _TOPICS[topic]
        selected = [check for check in assessment.checks if keys is None or check.check_id in keys]
        parts = []
        for check in selected:
            packet = EvidencePacket.model_validate(check.evidence_packet.model_dump())
            text = check.note
            calculations = []
            if check.result != "needs_review":
                if check.required is not None and check.earned is not None and check.gap is not None:
                    text += f" 기준 {check.required}, 확인된 이수 {check.earned}, 부족분 {check.gap}입니다."
                    calculations = [Calculation(metric=check.check_id, required=check.required, earned=check.earned, gap=check.gap)]
                if check.missing_courses:
                    text += " 남은 필수과목: " + ", ".join(check.missing_courses) + "."
                elif not check.check_id.startswith("credits."):
                    text += " 이 항목의 확인된 이수 조건을 충족했습니다."
            text += " 확인된 기록의 부분 비교이며 최종 졸업 인증이나 개인 면제·대체 판정은 아닙니다."
            parts.append(AssistantPart(title=check.label, text=text, status=packet.status, evidence_packet=packet, calculations=calculations))
        return parts, assessment

    def _fallback(self, request, question, plan_status):
        # Evidence survives provider failure, but a failed interpretation never
        # masquerades as a successful model answer or genuine missing source.
        catalogue = (getattr(self.engine.registry, "catalogue", None) or {}).get("courses", [])
        mentioned = resolve_course_mentions(self.engine.registry, question)
        parts = []
        for raw in catalogue:
            if raw["course_code"] in mentioned:
                part, _ = self._courses({"name": raw["course_code"]}, len(parts) + 1)
                parts.append(part)
                if len(parts) == 4:
                    break
        if not parts:
            result = self.engine.answer(AcademicAnswerRequest(question=question,
                admission_year=request.admission_year, matched_curriculum_year=request.matched_curriculum_year, department=request.department, earned_credits=request.earned_credits))
            if result.status == "supported":
                parts = [AssistantPart(title="확인된 근거", text=result.answer, status=result.status, evidence_packet=result.evidence_packet, calculations=result.calculations)]
        parts.append(self._unsupported("처리 상태", "질문 해석 또는 문장 생성 처리가 완료되지 않았습니다. 이는 학사 근거가 없다는 뜻이 아닙니다. 확인된 자료는 위에 표시했습니다. 다시 시도하거나 과목·요건을 나누어 질문해 주세요."))
        return self._response(request, parts, plan_status, reason="processing_unavailable")

    def chat(self, request: AssistantTurnRequest) -> AssistantTurnResponse:
        request = AssistantTurnRequest.model_validate(request.model_dump())
        question = current_transcript_question(request.question) if request.transcript is not None else request.question
        self.engine.validate_request(AcademicAnswerRequest(question=question, admission_year=request.admission_year,
            matched_curriculum_year=request.matched_curriculum_year, department=request.department, earned_credits=request.earned_credits))
        previous = request.previous_question
        if previous is not None:
            previous = current_transcript_question(previous) if request.transcript is not None else previous
            validate_public_text_safety(previous)
        registry = self.engine.registry
        if (request.admission_year, request.matched_curriculum_year, normalize_text(request.department)) != (2026, 2026, "컴퓨터공학과") or _exceeds_scope(question, registry) or previous and _exceeds_scope(previous, registry):
            return self._response(request, [self._unsupported("지원 범위", "2026학번·2026 교육과정·컴퓨터공학과의 확인된 근거만 지원합니다.", "out_of_scope")], "fallback", reason="unsupported_scope", kind="refusal")
        if _EXECUTION.search(question) or previous and _EXECUTION.search(previous):
            return self._response(request, [self._unsupported("질문 확인", "실행 지시가 아닌 과목이나 이수 기준을 질문해 주세요.", "out_of_scope")], "rejected", reason="unsupported_scope", kind="refusal")
        registry = self.engine.registry
        typed_provider = getattr(self.llm, "typed_plans", False) is True
        current_codes = resolve_course_mentions(registry, question)
        current_properties = [] if typed_provider else _requested_properties(question)
        previous_properties = [] if typed_provider else _requested_properties(previous or "")
        current_purpose = "attributes" if typed_provider else _requested_purpose(question)
        previous_purpose = "attributes" if typed_provider else _requested_purpose(previous or "")
        inherited = False
        # These shortcuts are only the explicitly untyped compatibility path.
        # A production provider receives the original safe question/context.
        if not typed_provider and previous and "과목 후보" in previous and not current_codes:
            choices = [fact for fact in (registry.catalogue or {}).get("courses", []) if fact["course_code"] in resolve_course_mentions(registry, previous)]
            choices.sort(key=lambda fact: previous.find(fact["course_name"]))
            selection = re.fullmatch(r"(?:그럼)?(?:([123])번?|([첫두세])(?:번째|째))(?:과목)?(?:요|으로|입니다)?", _compact(question))
            if selection:
                index = int(selection[1]) - 1 if selection[1] else {"첫": 0, "두": 1, "세": 2}[selection[2]]
                if index < len(choices):
                    question = choices[index]["course_name"]
                    current_codes = {choices[index]["course_code"]}
        if not typed_provider and _bare_entity(registry, question, current_codes):
            if not previous_properties and previous_purpose == "attributes":
                names = [fact["course_name"] for fact in (registry.catalogue or {}).get("courses", []) if fact["course_code"] in current_codes]
                return self._response(request, [self._unsupported("확인할 정보", ", ".join(names) + "의 학점·이수구분·편성·이수 의무 중 어떤 정보를 확인할까요?")],
                                      "fallback", "not_applicable", reason="ambiguous", kind="clarification", context_question=" 및 ".join(names) + " 과목 정보 확인")
            question += " " + ("이수 의무" if previous_purpose == "completion_obligation" else "강의 내용" if previous_purpose == "description" else " ".join(_PROPERTY_WORDS[prop] for prop in previous_properties))
            current_properties = previous_properties
            current_purpose = previous_purpose
            inherited = True
        if not typed_provider and not current_codes and set(current_properties) & {"credits", "category", "offering", "code"}:
            mentioned_rules = _rule_mentions(registry, question)
            category_rules = {key for entry in registry.intents["intents"] if entry["intent_id"] in {"credits.major.required", "credits.major.elective", "major.required-course-set"} for key in entry["rule_ids"]}
            if not mentioned_rules or mentioned_rules <= category_rules:
                candidates = course_candidates(registry, question)
                if candidates:
                    return self._course_clarification(request, candidates, current_properties)
        prior_codes = resolve_course_mentions(registry, previous or "")
        if not typed_provider and not current_codes and len(prior_codes) > 1 and re.search(r"그|이과목|해당과목", _compact(question)) and not re.search(r"둘|두과목|각각", _compact(question)):
            candidates = [fact for fact in (registry.catalogue or {}).get("courses", []) if fact["course_code"] in prior_codes]
            return self._course_clarification(request, candidates[:3], current_properties or previous_properties, True)
        payload = {"question": question, "previous_question": previous, "has_transcript": request.transcript is not None, "catalog": self._public_catalog()}
        demands = {} if typed_provider else _named_demands(registry, question, current_codes)
        plan_status = "unavailable"
        try:
            with self.llm.session() as session:
                result = session.plan(payload)
                plan_status = "rejected"
                plan = _plan(result, self.engine.registry, previous)
                if typed_provider and not result.typed_plan:
                    raise LLMInvalidResponse("typed provider returned an untyped plan")
                legacy_plan = not result.typed_plan
                if inherited and legacy_plan:
                    plan = {**plan, "context_used": True}
                overview = any(item["kind"] == "requirements_overview" for item in plan["requests"]) if not legacy_plan else _overview_requested(question)
                if overview:
                    if not any(item["kind"] == "requirements_overview" for item in plan["requests"]):
                        raise LLMInvalidResponse("graduation overview incorrectly routed to a catalogue")
                # Untyped compatibility only: production facets/filters are
                # already validated and must never be inferred or intersected
                # with a second keyword interpretation of the question.
                for item in plan["requests"]:
                    if legacy_plan and item["kind"] == "courses" and "name" in item["filters"] and ("purpose" in item or "properties" in item):
                        item["filters"] = {"name": item["filters"]["name"]}
                        if current_properties and item.get("purpose", "attributes") == "attributes":
                            selected_codes = resolve_course_mentions(registry, item["filters"]["name"])
                            allowed = set().union(*(demands.get(code, (set(current_properties), "attributes"))[0] for code in selected_codes)) if selected_codes else set(current_properties)
                            item["properties"] = [key for key in item.get("properties", current_properties) if key in allowed]
                count_categories = set()
                if legacy_plan and "count" in current_properties:
                    for category, pattern in (("major_required", r"전공필수|전필"), ("major_elective", r"전공선택|전선")):
                        if re.search(pattern, _compact(question)):
                            count_categories.add(category)
                if legacy_plan and not overview and any(item["kind"] == "requirements_overview" for item in plan["requests"]):
                    if not count_categories:
                        raise LLMInvalidResponse("unrequested graduation overview")
                    plan["requests"] = [item for item in plan["requests"] if item["kind"] != "requirements_overview"]
                if len(count_categories) == 2:
                    existing = {item["filters"].get("category") for item in plan["requests"] if item["kind"] == "courses"}
                    typed = result.typed_plan or not plan["requests"] or any("properties" in item or "purpose" in item for item in plan["requests"] if item["kind"] == "courses")
                    common_filters = {key: int(match[1]) for key, pattern in (("year", r"([1-4])\s*학년"), ("semester", r"([12])\s*학기")) if (match := re.search(pattern, question))}
                    for category in sorted(count_categories - existing):
                        plan["requests"].append({"kind": "courses", "filters": {"category": category, **common_filters}, **({"purpose": "attributes", "properties": ["count"]} if typed else {})})
                    if len(plan["requests"]) > 4:
                        raise LLMInvalidResponse("compound course request outside bounds")
                plan_status = "cached" if result.cached else "generated"
                current_mentions = resolve_course_mentions(self.engine.registry, question) if legacy_plan else set()
                effective = question if current_mentions else question + (" " + previous if plan["context_used"] else "")
                mentioned = (current_mentions or resolve_course_mentions(self.engine.registry, effective)) if legacy_plan else set().union(*(resolve_course_mentions(registry, item["filters"].get("name", "")) for item in plan["requests"] if item["kind"] == "courses"))
                named = bool(mentioned)
                planned_intents = {intent for item in plan["requests"] if item["kind"] == "rule" for intent in item["intent_ids"]}
                planned_rules = {key for entry in self.engine.registry.intents["intents"] if entry["intent_id"] in planned_intents for key in entry["rule_ids"]}
                planned_facts = [{"outcome": self.engine.registry.rules[key]["decision"]["outcome"]} for key in planned_rules]
                requirement_subject = bool(mentioned & _policy_course_coverage(self.engine.registry, planned_facts))
                # A rule's course label proves the rule's predicate only. For
                # actual requested attributes, retrieve CourseFacts separately.
                if legacy_plan and named and not overview and planned_intents:
                    for code in sorted(mentioned):
                        props, purpose = demands.get(code, (set(current_properties), current_purpose))
                        requested = props & {"credits", "category", "offering", "code"}
                        if purpose == "attributes" and requested and not any(item["kind"] == "courses" and code in resolve_course_mentions(registry, item["filters"].get("name", "")) for item in plan["requests"]):
                            if not requirement_subject:
                                raise LLMInvalidResponse("course attributes replaced by unrelated policy")
                            fact = next(fact for fact in registry.catalogue["courses"] if fact["course_code"] == code)
                            plan["requests"].append({"kind": "courses", "filters": {"name": fact["course_name"]}, "purpose": "attributes", "properties": sorted(requested)})
                    if len(plan["requests"]) > 4:
                        raise LLMInvalidResponse("recovered attributes exceed request bound")
                if legacy_plan and named and not overview and not requirement_subject and not any(item["kind"] == "courses" for item in plan["requests"]):
                    raise LLMInvalidResponse("named course incorrectly routed to a graduation rule")
                if legacy_plan and named and not overview and not requirement_subject and not re.search(r"목록|전체|모두|과목들|다\s*알려", effective):
                    for item in plan["requests"]:
                        if item["kind"] == "courses":
                            selected = resolve_course_mentions(self.engine.registry, item["filters"].get("name", ""))
                            if not selected or not selected <= mentioned:
                                raise LLMInvalidResponse("planner replaced the named subject with an unrelated list")
                if len(plan["requests"]) == 1 and plan["requests"][0]["kind"] == "greeting":
                    if hasattr(session, "accept"):
                        session.accept()
                    return self._response(request, [], plan_status, "not_applicable", kind="greeting")
                parts, specs, used_metrics, handled_subjects = [], [], set(), set()
                assessment = None
                for item in plan["requests"]:
                    kind = item["kind"]
                    if kind == "rule":
                        part, spec, metrics = self._rules(request, item["intent_ids"], len(parts) + 1)
                        used_metrics |= metrics
                        parts.append(part)
                        if part.status == "supported":
                            specs.append(spec)
                    elif kind == "courses":
                        purpose = item.get("purpose", "attributes")
                        properties = item.get("properties", current_properties if "purpose" in item else None)
                        if "name" in item["filters"]:
                            candidates = course_candidates(registry, item["filters"]["name"])
                            if not resolve_course_mentions(registry, item["filters"]["name"]) and candidates:
                                return self._course_clarification(request, candidates, properties or current_properties, plan["context_used"])
                        if purpose == "description":
                            handled_subjects.update(resolve_course_mentions(registry, item["filters"].get("name", "")))
                            parts.append(self._unsupported("강의 내용 근거", "과목의 학점·편성표는 강의 내용을 증명하지 않습니다. 강의계획서 근거가 없어 내용을 추정할 수 없습니다."))
                            if not properties:
                                continue
                        if purpose == "completion_obligation":
                            if "name" not in item["filters"]:
                                parts.append(self._unsupported("이수 의무", "확인할 과목을 지정해 주세요.")); continue
                            selected, selected_specs = self._obligation(request, item["filters"], len(parts) + 1)
                            handled_subjects.update(resolve_course_mentions(registry, item["filters"]["name"]))
                            parts.extend(selected); specs.extend(selected_specs)
                            # Purpose and attributes are independent facets;
                            # an obligation query can also ask for credits etc.
                            if not properties:
                                continue
                        if properties == []:
                            handled_subjects.update(resolve_course_mentions(registry, item["filters"].get("name", "")))
                            parts.append(self._unsupported("확인할 정보", "과목의 학점·이수구분·편성 중 확인할 속성을 알려 주세요.")); continue
                        part, spec = self._courses(item["filters"], len(parts) + 1, properties)
                        parts.append(part)
                        if spec:
                            specs.append(spec)
                    elif kind == "requirements_overview":
                        selected, selected_specs, metrics = self._overview(request, len(parts) + 1)
                        parts.extend(selected); specs.extend(selected_specs); used_metrics |= metrics
                    elif kind == "transcript":
                        selected, assessment = self._transcript(request, item["topic"], assessment)
                        parts.extend(selected)
                    elif kind != "greeting":
                        parts.append(self._unsupported("추가 확인", "질문의 개인 조건·대체·일정이나 추가 항목을 확인할 근거가 충분하지 않습니다. 확인하려는 항목과 조건을 구체적으로 알려 주세요.", "out_of_scope" if kind == "out_of_scope" else "insufficient_evidence"))
                compact = _compact(effective)
                pending = legacy_plan and (any(alias in compact for alias in self.engine.registry.intents["exception_aliases"])
                           or re.search(r"자동|대체할|대체해|이번학기|뭐부터|졸업가능|졸업할수", compact)
                           or "면제" in compact and "학석사" not in compact
                           or re.search(r"(?:제가|내가|저는|나도|저도).*(?:면제|인정|pass|패스|수강가능)", compact)
                           or re.search(r"장학금|기숙사|등록금|정확한.*(?:날짜|기한|마감)|언제까지", compact))
                for spec in specs:
                    if not legacy_plan or spec["family"] != "rules":
                        continue
                    for fact in spec["facts"]:
                        outcome = fact["outcome"]
                        if "최대" in compact and outcome.get("comparator") == "at_least" or "최소" in compact and outcome["type"] == "credit_recognition_cap":
                            pending = True
                if pending and all(part.status == "supported" for part in parts):
                    parts.append(self._unsupported("적용 조건 확인", "위의 일반 근거만으로 질문의 개인 적용·추가 조건이나 반대 방향의 제한을 확정할 수 없습니다. 공식 적용 내역과 확인할 조건을 알려 주세요."))
                covered = handled_subjects | {fact.course_code for part in parts if part.course_evidence is not None for fact in part.course_evidence.courses}
                for part in parts:
                    if part.evidence_packet is not None and part.status == "supported":
                        covered.update(_policy_course_coverage(registry, [{"outcome": registry.rules[item.rule_id]["decision"]["outcome"]} for item in part.evidence_packet.applied_rules]))
                for spec in specs:
                    if spec["family"] == "rules":
                        covered.update(_policy_course_coverage(self.engine.registry, spec["facts"]))
                    elif spec["family"] == "obligation":
                        covered.update(resolve_course_mentions(registry, " ".join(spec["subjects"])))
                missing_subjects = mentioned - covered
                if missing_subjects and not any(item["kind"] == "transcript" for item in plan["requests"]):
                    parts.append(self._unsupported("질문 해석 확인", "질문한 모든 과목을 현재 조회 결과에 연결하지 못했습니다. 누락한 과목을 포함해 다시 확인해야 합니다. 이는 해당 과목의 자료가 없다는 뜻은 아닙니다."))
                # Provider plans are not authority for completeness. For typed
                # named requests, verify both the demanded purpose and facets;
                # old plans intentionally keep strict full-fact compatibility.
                missing_properties = False
                typed_named = [item for item in plan["requests"] if item["kind"] == "courses" and "name" in item["filters"] and ("purpose" in item or "properties" in item)]
                if legacy_plan and typed_named and not overview:
                    for code in mentioned:
                        relevant = [item for item in typed_named if code in resolve_course_mentions(registry, item["filters"]["name"])]
                        facets = {prop for item in relevant if item.get("purpose", "attributes") == "attributes" for prop in item.get("properties", current_properties)}
                        demanded_properties, demanded_purpose = demands.get(code, (set(current_properties), current_purpose))
                        purpose_covered = any(item.get("purpose", "attributes") == demanded_purpose for item in relevant)
                        if not purpose_covered or demanded_purpose == "attributes" and demanded_properties - facets:
                            missing_properties = True
                    if missing_properties:
                        parts.append(self._unsupported("요청 항목 확인", "요청한 과목 속성 또는 목적 중 조회 계획에 연결되지 않은 항목이 있습니다. 확인된 근거는 유지하되 누락한 항목을 다시 확인해야 합니다."))
                expected_rules = _rule_mentions(self.engine.registry, effective) if legacy_plan and planned_intents else set()
                if legacy_plan and not planned_intents and any(item["kind"] == "courses" for item in plan["requests"]):
                    # A course tool cannot silently swallow a separate credit
                    # requirement. Mask exact course identities first so a
                    # thesis-credit question is not a thesis-completion claim.
                    remainder = normalize_course(effective)
                    tokens = [fact[key] for fact in (registry.catalogue or {}).get("courses", []) if fact["course_code"] in mentioned for key in ("course_name", "course_code")]
                    tokens += [alias for alias, name in COURSE_ALIASES.items() if resolve_course_mentions(registry, name) & mentioned]
                    for token in sorted(tokens, key=len, reverse=True):
                        remainder = remainder.replace(normalize_course(token), "")
                    expected_rules = _rule_mentions(registry, remainder)
                    if not re.search(r"총|요건|기준|필요|최소|최대|부족", remainder):
                        expected_rules -= {key for entry in registry.intents["intents"] if entry["intent_id"] in {"credits.major.required", "credits.major.elective", "major.required-course-set"} for key in entry["rule_ids"]}
                supplied_rules = {applied.rule_id for part in parts if part.evidence_packet is not None for applied in part.evidence_packet.applied_rules}
                missing_rules = _uncovered_rule_subjects(self.engine.registry, expected_rules, supplied_rules)
                if missing_rules and not any(part.status == "conflict" for part in parts):
                    parts.append(self._unsupported("이수 기준 확인", "질문에 포함된 이수 기준 중 현재 답변의 근거에 연결되지 않은 항목이 있습니다. 확인된 기준은 유지하되 누락된 항목을 다시 조회해야 합니다."))
                if any(item["kind"] in {"rule", "requirements_overview"} for item in plan["requests"]) and set(request.earned_credits) - used_metrics:
                    raise ValueError("earned-credit metric unrelated to selected requirements")
                if len(parts) > 8:
                    # Never hide unsupported verification holds to fit a UI cap.
                    return self._response(request, [self._unsupported("비교 항목 선택", "한 번에 비교할 항목이 많습니다. 졸업학점·교양학점·전공학점·필수과목 중 하나를 선택해 주세요.")], plan_status, reason="ambiguous", kind="clarification")
                generation_status = "not_applicable"
                processing_failure = bool(missing_subjects or missing_rules or missing_properties) and not any(item["kind"] == "transcript" for item in plan["requests"])
                if specs:
                    writer_payload = {"version": "1.0.0", "parts": [{**{key: spec[key] for key in ("part_id", "title", "facts")},
                        **({"properties": spec["properties"]} if "properties" in spec else {}),
                        **({"course_summary": spec["course_summary"]} if spec.get("course_summary") is not None else {})} for spec in specs]}
                    try:
                        written = session.write(writer_payload)
                        texts = _write(written, specs)
                        for text, spec in zip(texts, specs, strict=True):
                            parts[spec["index"]] = parts[spec["index"]].model_copy(update={"text": (text + "\n" + spec["local"]).strip()})
                        generation_status = "cached" if written.cached else "generated"
                    except (LLMUnavailable, LLMInvalidResponse, LLMBusy, OSError, TimeoutError):
                        generation_status = "fallback"
                        processing_failure = True
                        parts[0] = parts[0].model_copy(update={"text": "문장 생성 처리가 완료되지 않아 확인된 근거를 그대로 안내합니다.\n" + parts[0].text})
                if not processing_failure and hasattr(session, "accept"):
                    session.accept()
                evidence_gap = any(part.course_evidence is not None and part.status != "supported" for part in parts) or any(item["kind"] == "courses" and item.get("purpose") in {"description", "completion_obligation"} for item in plan["requests"]) and any(part.status != "supported" for part in parts)
                reason = "processing_unavailable" if processing_failure else ("pending_review" if any(part.status == "conflict" for part in parts) else "missing_student_input" if any(item["kind"] == "transcript" for item in plan["requests"]) and request.transcript is None else "no_matching_evidence" if evidence_gap else "ambiguous" if any(part.status != "supported" for part in parts) else None)
                focus = []
                for item in plan["requests"]:
                    if item["kind"] == "courses":
                        filters = item["filters"]
                        label = filters.get("name", "")
                        codes = resolve_course_mentions(self.engine.registry, label)
                        if codes:
                            label = " 및 ".join(raw["course_name"] for raw in (self.engine.registry.catalogue or {}).get("courses", []) if raw["course_code"] in codes)
                        label += (f" {filters['year']}학년" if "year" in filters else "")
                        label += (f" {filters['semester']}학기" if "semester" in filters else "")
                        label += " 전공필수" if filters.get("category") == "major_required" else " 전공선택" if filters.get("category") == "major_elective" else ""
                        purpose = item.get("purpose", "attributes")
                        property_words = " ".join((["이수 의무"] if purpose == "completion_obligation" else ["강의 내용"] if purpose == "description" else []) + [_PROPERTY_WORDS[key] for key in item.get("properties", current_properties)])
                        focus.append((label.strip() or "전체") + " " + (property_words or "과목 정보"))
                    elif item["kind"] == "rule":
                        focus.append(canonical_question(self.engine, item["intent_ids"]))
                    elif item["kind"] == "requirements_overview":
                        focus.append("졸업요건 전체 요약")
                    elif item["kind"] == "transcript":
                        focus.append({"graduation_credits": "졸업 부족학점", "general_credits": "교양 부족학점", "major_credits": "전공 부족학점", "required_courses": "남은 전공필수 과목", "thesis": "졸업논문 이수", "counseling": "심층상담 이수", "verification": "확인할 이수 항목"}[item["topic"]])
                return self._response(request, parts, plan_status, generation_status, plan["context_used"], reason, context_question=" 및 ".join(focus)[:500] or None)
        except (LLMInvalidResponse, LLMUnavailable, LLMBusy, OSError, TimeoutError, RegistryUnavailable) as exc:
            return self._fallback(request, question, "rejected" if isinstance(exc, LLMInvalidResponse) else "unavailable")
