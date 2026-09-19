from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

RULE_FILES = (
    "cwnu.cs.2026.cohort.department-transfer-original-admission-year.json",
    "cwnu.cs.2026.course-counting.identical-course.json",
    "cwnu.cs.2026.course-counting.post-completion-equivalence.json",
    "cwnu.cs.2026.course-counting.retake.json",
    "cwnu.cs.2026.credits.general-balanced.json",
    "cwnu.cs.2026.credits.general-foundation.json",
    "cwnu.cs.2026.credits.general-recognition-cap.json",
    "cwnu.cs.2026.credits.general-remaining-allocation.json",
    "cwnu.cs.2026.credits.general-remaining.json",
    "cwnu.cs.2026.credits.general-total.json",
    "cwnu.cs.2026.credits.graduation-remaining.json",
    "cwnu.cs.2026.credits.graduation-remaining-allocation.json",
    "cwnu.cs.2026.credits.graduation-total.json",
    "cwnu.cs.2026.credits.major-advanced.json",
    "cwnu.cs.2026.credits.major-elective.json",
    "cwnu.cs.2026.credits.major-minimum.json",
    "cwnu.cs.2026.credits.major-required.json",
    "cwnu.cs.2026.credits.major-total.json",
    "cwnu.cs.2026.general-balanced-area-coverage.json",
    "cwnu.cs.2026.general-recommended-courses.json",
    "cwnu.cs.2026.graduation.thesis-completion-result.json",
    "cwnu.cs.2026.graduation.thesis-linked-program-exemption.json",
    "cwnu.cs.2026.graduation.thesis-required.json",
    "cwnu.cs.2026.graduation.thesis-substitution.json",
    "cwnu.cs.2026.major-counseling-completion.json",
    "cwnu.cs.2026.major-required-course-set.json",
)
SOURCE_FILES = (
    "cwnu.curriculum.2026.changwon-undergraduate.json",
    "cwnu.curriculum.2026.ta-validation-response.json",
)
RESEARCH_FILE = "cwnu.cs.2026.graduation-practices.json"
EXPECTED_INTENTS = {
    "cohort.department-transfer.original-admission-year": ("specific", ("cwnu.cs.2026.cohort.department-transfer-original-admission-year",)),
    "course-counting.identical-course": ("specific", ("cwnu.cs.2026.course-counting.identical-course",)),
    "course-counting.post-completion-equivalence": ("specific", ("cwnu.cs.2026.course-counting.post-completion-equivalence",)),
    "course-counting.retake": ("specific", ("cwnu.cs.2026.course-counting.retake",)),
    "credits.general.balanced": ("specific", ("cwnu.cs.2026.credits.general-balanced",)),
    "credits.general.foundation": ("specific", ("cwnu.cs.2026.credits.general-foundation",)),
    "credits.general.recognition-cap": ("specific", ("cwnu.cs.2026.credits.general-recognition-cap",)),
    "credits.general.remaining-allocation": ("specific", ("cwnu.cs.2026.credits.general-remaining-allocation",)),
    "credits.general.remaining": ("specific", ("cwnu.cs.2026.credits.general-remaining",)),
    "credits.general.total": ("specific", ("cwnu.cs.2026.credits.general-total",)),
    "credits.graduation.remaining": ("specific", ("cwnu.cs.2026.credits.graduation-remaining",)),
    "credits.graduation.remaining-allocation": ("specific", ("cwnu.cs.2026.credits.graduation-remaining-allocation",)),
    "credits.graduation.total": ("specific", ("cwnu.cs.2026.credits.graduation-total",)),
    "credits.major.advanced": ("specific", ("cwnu.cs.2026.credits.major-advanced",)),
    "credits.major.elective": ("specific", ("cwnu.cs.2026.credits.major-elective",)),
    "credits.major.minimum": ("specific", ("cwnu.cs.2026.credits.major-minimum",)),
    "credits.major.required": ("specific", ("cwnu.cs.2026.credits.major-required",)),
    "credits.major.total": ("specific", ("cwnu.cs.2026.credits.major-total",)),
    "general.balanced-area-coverage": ("specific", ("cwnu.cs.2026.general-balanced-area-coverage",)),
    "general.recommended-courses": ("specific", ("cwnu.cs.2026.general-recommended-courses",)),
    "graduation.thesis.completion-result": ("specific", ("cwnu.cs.2026.graduation.thesis-completion-result",)),
    "graduation.thesis.linked-program-exemption": ("specific", ("cwnu.cs.2026.graduation.thesis-linked-program-exemption",)),
    "graduation.thesis.required": ("specific", ("cwnu.cs.2026.graduation.thesis-required",)),
    "graduation.thesis.substitution": ("specific", ("cwnu.cs.2026.graduation.thesis-substitution",)),
    "major.counseling-completion": ("specific", ("cwnu.cs.2026.major-counseling-completion",)),
    "major.required-course-set": ("specific", ("cwnu.cs.2026.major-required-course-set",)),
    "credits.general.bundle": ("bundle", ("cwnu.cs.2026.credits.general-balanced", "cwnu.cs.2026.credits.general-foundation", "cwnu.cs.2026.credits.general-remaining", "cwnu.cs.2026.credits.general-total")),
    "credits.major.bundle": ("bundle", ("cwnu.cs.2026.credits.major-advanced", "cwnu.cs.2026.credits.major-elective", "cwnu.cs.2026.credits.major-minimum", "cwnu.cs.2026.credits.major-required", "cwnu.cs.2026.credits.major-total")),
}


class RegistryUnavailable(RuntimeError):
    """Raised without filesystem details when trusted registry loading fails."""

    def __init__(self) -> None:
        super().__init__("academic registry unavailable")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _read_json_once(path: Path) -> Any:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8"))


@dataclass(frozen=True)
class Registry:
    rules: dict[str, dict[str, Any]]
    rule_hashes: dict[str, str]
    sources: dict[str, dict[str, Any]]
    research: dict[str, Any]
    intents: dict[str, Any]
    digest: str
    conflicts: dict[str, tuple[str, ...]]

    @classmethod
    def load(cls, project_root: Path | str | None = None) -> "Registry":
        try:
            root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
            rule_schema = _read_json_once(root / "contracts" / "rule-fact.schema.json")
            source_schema = _read_json_once(root / "contracts" / "source-entry.schema.json")
            intent_schema = _read_json_once(root / "contracts" / "academic-intent-profile.schema.json")
            intents = _read_json_once(root / "config" / "academic-intents.json")
            pins = _read_json_once(root / "config" / "academic-registry-pins.json")
            source_values = [_read_json_once(root / "knowledge" / "sources" / name) for name in SOURCE_FILES]
            research = _read_json_once(root / "reviews" / "academic" / "research" / RESEARCH_FILE)
            rule_values = [_read_json_once(root / "knowledge" / "rules" / name) for name in RULE_FILES]
            for source in source_values:
                Draft202012Validator(source_schema).validate(source)
            Draft202012Validator(intent_schema).validate(intents)
            validator = Draft202012Validator(rule_schema)
            for rule in rule_values:
                validator.validate(rule)
            cls._validate_profile(rule_values, source_values, research, intents, pins)
            rules = {rule["rule_id"]: rule for rule in rule_values}
            sources = {source["source_id"]: source for source in source_values}
            hashes = {rule_id: canonical_sha256(rule) for rule_id, rule in rules.items()}
            digest = canonical_sha256({
                "rules": hashes,
                "sources": {source_id: canonical_sha256(source) for source_id, source in sorted(sources.items())},
                "research": canonical_sha256(research),
                "intents": canonical_sha256(intents),
            })
            return cls(rules, hashes, sources, research, intents, digest, cls._semantic_conflicts(rules))
        except Exception as exc:
            if isinstance(exc, RegistryUnavailable):
                raise
            raise RegistryUnavailable() from None

    @staticmethod
    def _validate_profile(rules: list[dict[str, Any]], source_values: list[dict[str, Any]], research: dict[str, Any], intents: dict[str, Any], pins: dict[str, Any]) -> None:
        expected_ids = {name[:-5] for name in RULE_FILES}
        if len(rules) != len(RULE_FILES) or {r.get("rule_id") for r in rules} != expected_ids:
            raise RegistryUnavailable()
        sources = {source.get("source_id"): source for source in source_values}
        expected_source_ids = {name[:-5] for name in SOURCE_FILES}
        if len(sources) != len(SOURCE_FILES) or set(sources) != expected_source_ids:
            raise RegistryUnavailable()
        for obj in [*source_values, *rules]:
            if obj.get("review") != {**obj.get("review", {})}:
                raise RegistryUnavailable()
            review = obj["review"]
            if (review.get("status"), review.get("mode"), review.get("scope")) != ("approved", "human", "full"):
                raise RegistryUnavailable()
        source_scope = sources["cwnu.curriculum.2026.changwon-undergraduate"].get("applicability", {})
        if source_scope.get("admission_years") != [2026] or source_scope.get("curriculum_years") != [2026] or source_scope.get("departments") != ["컴퓨터공학과"]:
            raise RegistryUnavailable()
        for rule in rules:
            scope = rule["applicability"]
            if scope.get("admission_years") != [2026] or scope.get("curriculum_years") != [2026] or scope.get("departments") != ["컴퓨터공학과"]:
                raise RegistryUnavailable()
            for evidence in rule["evidence"]:
                if evidence["source_id"] not in sources:
                    raise RegistryUnavailable()
            targets = rule["relationship"]["target_rule_ids"]
            if any(target not in expected_ids for target in targets):
                raise RegistryUnavailable()
        if research.get("research_id") != "cwnu.cs.2026.graduation-practices" or research.get("eligible_for_academic_answer") is not False:
            raise RegistryUnavailable()
        if any(c.get("status") != "unverified" or c.get("eligible_for_academic_answer") is not False or c.get("answer_status_until_verified") != "insufficient_evidence" for c in research.get("claims", [])):
            raise RegistryUnavailable()
        conditions = "".join(source_scope.get("conditions", [])).replace(" ", "")
        if any(alias not in conditions for alias in ("재입학", "전과", "편입", "경과조치")):
            raise RegistryUnavailable()
        configured = {r for entry in intents.get("intents", []) for r in entry.get("rule_ids", [])}
        if configured != expected_ids:
            raise RegistryUnavailable()
        entries = intents.get("intents", [])
        if len({entry.get("intent_id") for entry in entries}) != len(EXPECTED_INTENTS):
            raise RegistryUnavailable()
        for entry in entries:
            expected = EXPECTED_INTENTS.get(entry.get("intent_id"))
            if expected is None or (entry.get("kind"), tuple(entry.get("rule_ids", []))) != expected:
                raise RegistryUnavailable()
        aliases = [alias for entry in intents.get("intents", []) for alias in entry.get("aliases", [])]
        if any(not alias or any(char.isspace() for char in alias) or alias != alias.lower() for alias in aliases):
            raise RegistryUnavailable()
        if set(pins) != {"schema_version", "algorithm", "rules", "sources", "research", "intents"} or pins.get("schema_version") != "1.0.0" or pins.get("algorithm") != "sha256-canonical-json-v1":
            raise RegistryUnavailable()
        computed_rules = {rule["rule_id"]: canonical_sha256(rule) for rule in rules}
        if pins.get("rules") != computed_rules:
            raise RegistryUnavailable()
        computed_sources = {source_id: canonical_sha256(source) for source_id, source in sources.items()}
        if pins.get("sources") != computed_sources:
            raise RegistryUnavailable()
        if pins.get("research") != {"id": research["research_id"], "digest": canonical_sha256(research)}:
            raise RegistryUnavailable()
        if pins.get("intents") != {"id": "academic-intents", "digest": canonical_sha256(intents)}:
            raise RegistryUnavailable()

    @staticmethod
    def _semantic_conflicts(rules: dict[str, dict[str, Any]]) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[tuple[str, str]]] = {}
        for rule_id, rule in rules.items():
            outcome = rule["decision"]["outcome"]
            identity = outcome.get("metric") or outcome.get("requirement") or outcome.get("target_requirement")
            key = f"{outcome['type']}:{identity}" if identity else None
            if key:
                grouped.setdefault(key, []).append((rule_id, canonical_sha256(outcome)))
        conflicts: dict[str, tuple[str, ...]] = {}
        for key, entries in grouped.items():
            if len({digest for _, digest in entries}) > 1:
                conflicts[key] = tuple(sorted(rule_id for rule_id, _ in entries))
        return conflicts

    @property
    def allowed_metrics(self) -> frozenset[str]:
        return frozenset(
            rule["decision"]["outcome"]["metric"]
            for rule in self.rules.values()
            if rule["decision"]["outcome"]["type"] == "credit_threshold"
        )
