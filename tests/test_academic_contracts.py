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
        "DSWRunRequest": "dsw-run-request.schema.json",
        "TaskResult": "task-result.schema.json",
        "CompletionReport": "completion-report.schema.json",
    }

    def test_all_six_examples_validate_against_live_schemas(self) -> None:
        for heading, schema_file in self.EXAMPLES.items():
            with self.subTest(contract=heading):
                validator(schema_file).validate(documented_example(heading))

    def test_all_six_contract_schemas_pass_meta_schema_validation(self) -> None:
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
