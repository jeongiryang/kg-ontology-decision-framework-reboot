from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from scripts.validation.validate_academic_clarifications import validate_clarifications
from scripts.validation.validate_academic_knowledge import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
RULE = "cwnu.cs.2026.operations.pccp-current-trial"


class OperationalLineageTest(unittest.TestCase):
    def test_closed_packet_and_conservative_relationship_correction(self):
        self.assertEqual(validate_clarifications(ROOT), [])
        ledger = json.loads((ROOT / "knowledge/relationship-corrections.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["corrections"]), 3)
        for entry in ledger["corrections"]:
            rule = json.loads((ROOT / f"knowledge/rules/{entry['rule_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(rule["relationship"], {"kind": "base", "target_rule_ids": []})
            self.assertEqual(canonical_sha256(rule), entry["current_sha256"])
            rule["relationship"] = entry["previous_relationship"]
            self.assertEqual(canonical_sha256(rule), entry["previous_sha256"])

    def test_correction_cannot_reapprove_changed_academic_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for directory in ("contracts", "knowledge", "reviews", "reports/source-audits"):
                shutil.copytree(ROOT / directory, root / directory)
            path = root / f"knowledge/rules/{RULE}.json"
            rule = json.loads(path.read_text(encoding="utf-8"))
            rule["decision"]["outcome"]["minimum_score"] = 600
            path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            ledger_path = root / "knowledge/relationship-corrections.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            for entry in ledger["corrections"]:
                if entry["rule_id"] == RULE:
                    entry["current_sha256"] = canonical_sha256(rule)
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            self.assertTrue(any("relationship-only lineage" in error for error in validate_clarifications(root)))

    def test_operational_answer_policy_is_required(self):
        schema = json.loads((ROOT / "contracts/rule-fact.schema.json").read_text(encoding="utf-8"))
        rule = json.loads((ROOT / f"knowledge/rules/{RULE}.json").read_text(encoding="utf-8"))
        rule.pop("answer_policy")
        errors = list(Draft202012Validator(schema).iter_errors(rule))
        self.assertTrue(any("answer_policy" in error.message for error in errors))


if __name__ == "__main__":
    unittest.main()
