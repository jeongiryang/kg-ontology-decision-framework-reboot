import copy
import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
CONTRACT_DOC = ROOT / "docs" / "harness" / "contracts.md"


def load_schema(filename: str) -> dict:
    return json.loads((CONTRACTS / filename).read_text(encoding="utf-8"))


def validator(filename: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(filename), format_checker=FormatChecker())


def documented_example(heading: str) -> dict:
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    match = re.search(
        rf"^## {re.escape(heading)}\s+.*?^```json\s*\n(.*?)^```",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"JSON example not found for {heading}")
    return json.loads(match.group(1))


class DocumentedContractExamplesTest(unittest.TestCase):
    EXAMPLES = {
        "SourceEntry": "source-entry.schema.json",
        "RuleFact": "rule-fact.schema.json",
        "EvidencePacket": "evidence-packet.schema.json",
        "AcademicReviewPacket": "academic-review-packet.schema.json",
        "AcademicClarificationPacket": "academic-clarification-packet.schema.json",
        "DSWRunRequest": "dsw-run-request.schema.json",
        "TaskResult": "task-result.schema.json",
        "CompletionReport": "completion-report.schema.json",
    }

    def test_all_documented_examples_validate_against_live_schemas(self) -> None:
        for heading, schema_file in self.EXAMPLES.items():
            with self.subTest(contract=heading):
                validator(schema_file).validate(documented_example(heading))

    def test_all_contract_schemas_pass_meta_schema_validation(self) -> None:
        for schema_file in self.EXAMPLES.values():
            with self.subTest(schema=schema_file):
                Draft202012Validator.check_schema(load_schema(schema_file))


class SourceEntrySecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = validator("source-entry.schema.json")
        self.valid = documented_example("SourceEntry")

    def test_repository_relative_locator_is_allowed(self) -> None:
        self.validator.validate(self.valid)

    def test_local_absolute_locators_are_rejected(self) -> None:
        bad_locators = (
            "C:/Users/example/source.pdf",
            "C:\\Users\\example\\source.pdf",
            "/home/example/source.pdf",
            "\\\\server\\share\\source.pdf",
            "file:///home/example/source.pdf",
        )
        for locator in bad_locators:
            instance = copy.deepcopy(self.valid)
            instance["canonical_locator"] = locator
            with self.subTest(locator=locator):
                self.assertFalse(self.validator.is_valid(instance))

    def test_curriculum_does_not_require_an_invented_effective_date(self) -> None:
        self.assertNotIn("document_dates", self.valid)
        self.validator.validate(self.valid)

    def test_curriculum_requires_admission_year_applicability(self) -> None:
        instance = copy.deepcopy(self.valid)
        del instance["applicability"]["admission_years"]
        self.assertFalse(self.validator.is_valid(instance))

    def test_approved_source_requires_human_review_record(self) -> None:
        for field in ("reviewer_id", "reviewed_at", "rationale"):
            instance = copy.deepcopy(self.valid)
            del instance["review"][field]
            with self.subTest(field=field):
                self.assertFalse(self.validator.is_valid(instance))


class RuleFactContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = validator("rule-fact.schema.json")
        self.valid = documented_example("RuleFact")

    def test_v1_shape_is_rejected(self) -> None:
        instance = copy.deepcopy(self.valid)
        instance["schema_version"] = "1.0.0"
        self.assertFalse(self.validator.is_valid(instance))

    def test_base_rule_cannot_target_another_rule(self) -> None:
        instance = copy.deepcopy(self.valid)
        instance["relationship"]["target_rule_ids"] = ["another.rule"]
        self.assertFalse(self.validator.is_valid(instance))

    def test_non_base_rule_requires_a_target(self) -> None:
        instance = copy.deepcopy(self.valid)
        instance["relationship"]["kind"] = "exception"
        self.assertFalse(self.validator.is_valid(instance))

    def test_admission_year_rule_requires_cohort(self) -> None:
        instance = copy.deepcopy(self.valid)
        del instance["applicability"]["admission_years"]
        self.assertFalse(self.validator.is_valid(instance))

    def test_none_listed_substitution_is_not_a_boolean_exemption(self) -> None:
        instance = copy.deepcopy(self.valid)
        instance["decision"] = {
            "statement": "현재 교육과정에는 졸업논문 대체수단이 기재되어 있지 않다.",
            "operator": "direct",
            "outcome": {
                "type": "substitution_policy",
                "target_requirement": "graduation.thesis",
                "listing_status": "none_listed",
            },
        }
        self.validator.validate(instance)
        instance["decision"]["outcome"]["required"] = False
        self.assertFalse(self.validator.is_valid(instance))


class AcademicReviewPacketTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = validator("academic-review-packet.schema.json")
        self.valid = documented_example("AcademicReviewPacket")

    def test_pending_review_subject_is_valid(self) -> None:
        self.validator.validate(self.valid)

    def test_completed_review_subject_requires_audit_fields(self) -> None:
        for status in ("approved", "rejected", "needs_revision"):
            instance = copy.deepcopy(self.valid)
            instance["subjects"][0]["status"] = status
            with self.subTest(status=status):
                self.assertFalse(self.validator.is_valid(instance))

class EvidencePacketSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = validator("evidence-packet.schema.json")
        self.valid = documented_example("EvidencePacket")

    def test_case_variant_pii_field_names_are_rejected(self) -> None:
        for field_name in (
            "Student_ID",
            "studentId",
            "student-id",
            "studentNumber",
            "STUDENT_NAME",
            "Name",
            "Raw_Transcript",
            "rawTranscript",
            "학번",
        ):
            instance = copy.deepcopy(self.valid)
            instance["student_facts"][field_name] = "prohibited"
            with self.subTest(field_name=field_name):
                self.assertFalse(self.validator.is_valid(instance))

    def test_student_number_values_are_rejected_recursively(self) -> None:
        prohibited_values = (
            "학번: 202612345",
            "202612345",
            202612345,
            {"note": "student id=202612345"},
            {"note": 202612345},
            ["safe", {"memo": "202612345"}],
            ["safe", {"memo": 202612345}],
        )
        for value in prohibited_values:
            instance = copy.deepcopy(self.valid)
            instance["student_facts"]["note"] = value
            with self.subTest(value=value):
                self.assertFalse(self.validator.is_valid(instance))

    def test_non_identifying_field_name_is_allowed(self) -> None:
        instance = copy.deepcopy(self.valid)
        instance["student_facts"]["completed_major_credits"] = 42
        self.validator.validate(instance)

    def test_supported_packet_cannot_keep_review_issues(self) -> None:
        instance = copy.deepcopy(self.valid)
        instance["status"] = "supported"
        instance["applied_rules"] = [
            {"rule_id": "cwnu.cs.2026.graduation.total-credits", "rule_sha256": "a" * 64}
        ]
        self.assertFalse(self.validator.is_valid(instance))


class DSWRunRequestSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = validator("dsw-run-request.schema.json")
        self.valid = documented_example("DSWRunRequest")

    def test_gpu_count_must_match_requested_gpu_id_count(self) -> None:
        mismatches = ((0, [0]), (1, [0, 1]), (2, [0]), (3, [0, 1]), (4, [0, 1, 2]))
        for gpu_count, gpu_ids in mismatches:
            instance = copy.deepcopy(self.valid)
            instance["gpu_count"] = gpu_count
            instance["requested_gpu_ids"] = gpu_ids
            with self.subTest(gpu_count=gpu_count, gpu_ids=gpu_ids):
                self.assertFalse(self.validator.is_valid(instance))

    def test_full_gpu_request_requires_announcement(self) -> None:
        instance = copy.deepcopy(self.valid)
        instance["gpu_count"] = 4
        instance["requested_gpu_ids"] = [0, 1, 2, 3]
        instance["command"] = "CUDA_VISIBLE_DEVICES=0,1,2,3 python evaluate.py"
        self.assertFalse(self.validator.is_valid(instance))

        instance["announcement_reference"] = "lab-notice-2026-09-17"
        self.validator.validate(instance)


if __name__ == "__main__":
    unittest.main()
