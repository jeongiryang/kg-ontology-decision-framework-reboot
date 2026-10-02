"""Source-verified catalogue facts and bounded read-only graph retrieval."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from .assistant_models import CourseFact, CourseCitation, CourseEvidencePacket
from .registry import canonical_bytes, canonical_sha256, RegistryUnavailable

SOURCE_ID = "cwnu.curriculum.2026.changwon-undergraduate"
CATALOGUE_PATH = "knowledge/course-catalogue.json"
PIN_PATH = "config/course-catalogue-pin.json"
SCOPE = {"admission_year": 2026, "matched_curriculum_year": 2026, "department": "컴퓨터공학과"}
COURSE_ALIASES = {
    "컴구": "컴퓨터구조", "고자구": "고급자료구조", "네프": "네트워크프로그래밍",
    "운체": "운영체제", "졸논": "졸업논문",
    "데이터베이스개론": "데이타베이스개론", "데이터통신": "데이타통신",
    "캡디1": "산학캡스톤디자인1", "캡디2": "산학캡스톤디자인2",
    "캡스톤1": "산학캡스톤디자인1", "캡스톤2": "산학캡스톤디자인2",
    "산학캡스톤디자인i": "산학캡스톤디자인1", "산학캡스톤디자인ii": "산학캡스톤디자인2",
}


def normalize_course(text: str) -> str:
    return re.sub(r"[\s·()\-]", "", unicodedata.normalize("NFKC", text).lower())


def resolve_course_mentions(registry, text: str) -> set[str]:
    """Resolve source identities/aliases, never individual question templates.

    Longest names consume their span first: 고급자료구조 is not an implicit
    second request for 자료구조, and roman II does not also mean roman I.
    """
    facts = (getattr(registry, "catalogue", None) or {}).get("courses", [])
    by_name = {normalize_course(fact["course_name"]): fact["course_code"] for fact in facts}
    identities = {normalize_course(fact[key]): fact["course_code"]
                  for fact in facts for key in ("course_name", "course_code")}
    identities.update({normalize_course(alias): by_name[normalize_course(name)]
                       for alias, name in COURSE_ALIASES.items() if normalize_course(name) in by_name})
    remaining = normalize_course(text)
    found = set()
    for token, code in sorted(identities.items(), key=lambda pair: len(pair[0]), reverse=True):
        if token in remaining:
            found.add(code)
            remaining = remaining.replace(token, " ")
    return found


def course_candidates(registry, text: str) -> list[dict]:
    """Bounded real-source candidates only; fuzzy scores never select a fact."""
    facts = (getattr(registry, "catalogue", None) or {}).get("courses", [])
    value = normalize_course(text)
    # Strip query structure, never arbitrary syllables inside a course name.
    # Fuzzy matching offers candidates only; it never selects academic facts.
    value = re.split(r"(?:몇|[0-9]+)?학점|전공필수|전공선택|전필|전선|과목|학년|학기|이수구분", value, maxsplit=1)[0]
    value = re.sub(r"(?:는|은|이|가|의|을|를|인가요|이야|요|[?])+$", "", value)
    if len(value) < 3:
        return []
    scored = [(SequenceMatcher(None, value, normalize_course(fact["course_name"])).ratio(), fact) for fact in facts]
    scored.sort(key=lambda pair: (-pair[0], pair[1]["course_code"]))
    if not scored or scored[0][0] < .58:
        return []
    return [{"course_code": fact["course_code"], "course_name": fact["course_name"]}
            for score, fact in scored[:2] if score >= max(.48, scored[0][0] - .24)]


def load_catalogue(root: Path, sources: dict) -> dict | None:
    path = root / CATALOGUE_PATH
    if not path.exists():
        # Old fixture roots can exercise the legacy APIs without this feature.
        return None
    try:
        value = json.loads(path.read_bytes())
        pin = json.loads((root / PIN_PATH).read_bytes())
        if set(value) != {"schema_version", "source_id", "source_sha256", "verification", "courses"} or value["schema_version"] != "1.0.0":
            raise ValueError("invalid catalogue")
        if canonical_sha256(value) != pin["catalogue_sha256"] or pin["source_sha256"] != value["source_sha256"]:
            raise ValueError("unbound catalogue")
        source = sources[value["source_id"]]
        if (value["source_id"] != SOURCE_ID or source["sha256"] != value["source_sha256"]
                or source["review"]["status"] != "approved"
                or value["verification"]["status"] != "source_verified"
                or not value["courses"]):
            raise ValueError("unapproved catalogue source")
        ids = set()
        for raw in value["courses"]:
            fact = CourseFact.model_validate(raw)
            if (fact.course_id in ids or fact.source_id != value["source_id"] or fact.source_sha256 != value["source_sha256"]
                    or fact.fact_sha256 != canonical_sha256({k: v for k, v in raw.items() if k != "fact_sha256"})):
                raise ValueError("invalid course fact")
            if not fact.offering_years or set(fact.offering_years) - {1, 2, 3, 4} or not fact.offering_semesters or set(fact.offering_semesters) - {"1", "2", "S"} or not fact.offering_label:
                raise ValueError("offering cells missing")
            ids.add(fact.course_id)
        return value
    except Exception:
        raise RegistryUnavailable() from None


def retrieve_courses(engine, filters: dict) -> CourseEvidencePacket:
    from .progress import emit
    if not isinstance(filters, dict) or set(filters) - {"name", "category", "year", "semester"}:
        raise ValueError("invalid course query")
    name, category = filters.get("name"), filters.get("category")
    year, semester = filters.get("year"), filters.get("semester")
    if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 100):
        raise ValueError("invalid course name")
    if category not in {None, "major_required", "major_elective"}:
        raise ValueError("invalid course category")
    if year is not None and (type(year) is not int or year not in {1, 2, 3, 4}):
        raise ValueError("invalid study year")
    if semester is not None and (type(semester) is not int or semester not in {1, 2}):
        raise ValueError("invalid semester")
    registry = engine.registry
    catalogue = getattr(registry, "catalogue", None)
    packet_id = "courses-" + hashlib.sha256(canonical_bytes({"filters": filters, "registry": registry.digest})).hexdigest()[:32]
    if catalogue is None:
        return CourseEvidencePacket(packet_id=packet_id, scope=SCOPE, status="insufficient_evidence", courses=[], evidence=[],
            issues=[{"kind": "missing", "message": "검증된 과목 목록을 아직 불러오지 못했습니다."}])
    if engine.evidence_reader is not None:
        records = engine.evidence_reader.fetch_courses(registry)
    else:
        records = catalogue["courses"]
        emit("query", "info", "로컬 검증 목록을 조회합니다.", details={"backend": "registry", "filters": dict(filters), "result_count": len(records)})
    # Identity aliases, not question templates or new equivalence rules.
    key = normalize_course(name) if name else None
    if key:
        key = normalize_course(COURSE_ALIASES.get(key, name))
    matches = []
    for raw in records:
        fact = CourseFact.model_validate(raw)
        if key and key not in {normalize_course(fact.course_name), normalize_course(fact.course_code)}:
            continue
        if category is not None and fact.category != category or year is not None and year not in (fact.offering_years or [fact.year]) or semester is not None and str(semester) not in (fact.offering_semesters or [str(fact.semester)]):
            continue
        matches.append(fact)
    if not matches:
        return CourseEvidencePacket(packet_id=packet_id, scope=SCOPE, status="insufficient_evidence", courses=[], evidence=[],
            issues=[{"kind": "missing", "message": "2026 교육과정의 검증된 과목 목록에서 해당 조건을 확인하지 못했습니다. 과목명이나 학년을 확인해 주세요."}])
    evidence = []
    emit("retrieval", "info", "조건에 맞는 과목 근거를 연결했습니다.", details={"backend": "neo4j" if engine.evidence_reader is not None else "registry", "result_count": len(matches)})
    for fact in matches:
        label = "전공필수" if fact.category == "major_required" else "전공선택"
        term = f", {fact.offering_label}" if fact.offering_label else f", {fact.year}학년 {fact.semester}학기" if fact.year is not None and fact.semester is not None else ""
        evidence.append(CourseCitation(source_id=fact.source_id, source_sha256=fact.source_sha256, locator=fact.locator,
            course_id=fact.course_id, claim=f"{fact.course_name}: {label}, {fact.credits}학점{term}."))
    return CourseEvidencePacket(packet_id=packet_id, scope=SCOPE, status="supported", courses=matches, evidence=evidence, issues=[])
