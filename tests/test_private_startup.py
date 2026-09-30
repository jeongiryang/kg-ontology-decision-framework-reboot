from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("private_startup",ROOT/"scripts/operations/start_prototype.py")
startup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class PrivateStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/".local").mkdir(exist_ok=True)

    def test_only_ignored_private_settings_are_loaded(self):
        with tempfile.TemporaryDirectory(dir=ROOT/".local") as directory:
            path=Path(directory)/"settings.json"
            path.write_text(json.dumps({"ACADEMIC_LLM_PROVIDER":"disabled"}),encoding="utf-8")
            self.assertEqual({"ACADEMIC_LLM_PROVIDER":"disabled"},startup.load_private_settings(path))
            for invalid in ({"PYTHONPATH":"elsewhere"},{"student_name":"not-allowed"},{"NEO4J_PASSWORD":5},{"NEO4J_PASSWORD":"line\nline"},[]):
                path.write_text(json.dumps(invalid),encoding="utf-8")
                with self.assertRaises(ValueError):startup.load_private_settings(path)

    def test_public_or_missing_configuration_is_rejected(self):
        with self.assertRaises(ValueError):startup.load_private_settings(ROOT/"config/pilot.env.example")
        with self.assertRaises(ValueError):startup.load_private_settings(ROOT/".local/nonexistent-settings.json")

    def test_duplicate_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/".local") as directory:
            path=Path(directory)/"settings.json"
            path.write_text('{"ACADEMIC_LLM_PROVIDER":"disabled","ACADEMIC_LLM_PROVIDER":"lmstudio"}',encoding="utf-8")
            with self.assertRaises(ValueError):startup.load_private_settings(path)


if __name__=="__main__":unittest.main()
