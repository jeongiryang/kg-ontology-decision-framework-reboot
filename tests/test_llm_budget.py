import threading
import unittest
from unittest.mock import patch

from academic_assistant import llm


class LLMBudgetTests(unittest.TestCase):
    def setUp(self):
        self.budget = patch.object(llm, "_budget", llm._RequestBudget())
        self.budget.start(); self.addCleanup(self.budget.stop)
        self.now = 100.0
        self.clock = patch.object(llm.time, "monotonic", side_effect=lambda: self.now)
        self.clock.start(); self.addCleanup(self.clock.stop)

    def client(self, timeout=3.0):
        return llm.LocalLLMClient(llm.LLMSettings("lmstudio", "http://127.0.0.1:1234", "test-model", timeout_seconds=timeout))

    def test_shared_cache_and_interval(self):
        a, b = self.client(), self.client()
        catalog = {"course.one":"label must never transmit"}
        with patch.object(a, "_request_intent", return_value="course.one") as call, patch.object(b, "_request_intent", return_value="course.one") as other:
            self.assertEqual("course.one", a.suggest_intent("course", catalog))
            self.assertEqual("course.one", b.suggest_intent("course", catalog))
            self.assertIsNone(b.suggest_intent("credits", catalog))
            self.assertEqual(0, other.call_count)
            self.now += 5
            self.assertEqual("course.one", b.suggest_intent("credits", catalog))
            self.assertEqual(1, call.call_count)
            self.assertEqual(1, other.call_count)

    def test_active_request_has_no_wait_queue(self):
        a, b = self.client(), self.client()
        entered, release = threading.Event(), threading.Event()
        def slow(*_): entered.set(); release.wait(2); return None
        with patch.object(a, "_request_intent", side_effect=slow), patch.object(b, "_request_intent") as other:
            worker = threading.Thread(target=a.suggest_intent, args=("course", {"course.one":"x"}))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertIsNone(b.suggest_intent("credits", {"course.one":"x"}))
                other.assert_not_called()
            finally:
                release.set(); worker.join(2)
            self.assertFalse(worker.is_alive())

    def test_failure_cooldown_shared_across_clients(self):
        a, b = self.client(), self.client()
        with patch.object(a, "_request_intent", side_effect=llm.LLMUnavailable("timeout")), patch.object(b, "_request_intent", return_value=None) as other:
            with self.assertRaises(llm.LLMUnavailable): a.suggest_intent("course", {"course.one":"x"})
            self.now += 59
            self.assertIsNone(b.suggest_intent("course", {"course.one":"x"}))
            other.assert_not_called()
            self.now += 1
            b.suggest_intent("course", {"course.one":"x"})
            self.assertEqual(1, other.call_count)

    def test_cache_bounded_expiring_and_endpoint_separated(self):
        client = self.client()
        with patch.object(client, "_request_intent", return_value=None):
            for index in range(40):
                client.suggest_intent("course", {f"course.item{index}":"x"}); self.now += 5
        self.assertEqual(32, len(llm._budget.cache))
        self.now += 301
        with patch.object(client, "_request_intent", return_value=None) as request:
            client.suggest_intent("course", {"course.item39":"x"}); request.assert_called_once()

    def test_limits_cannot_disable_budget(self):
        for value in (float("nan"), 0, 1, 601):
            with self.assertRaises(ValueError):
                llm.LLMSettings("lmstudio", "http://127.0.0.1:1234", "x", min_interval_seconds=value)
        with self.assertRaises(ValueError):
            llm.LLMSettings("lmstudio", "http://127.0.0.1:1234", "x", timeout_seconds=float("nan"))

    def test_cold_start_timeout_is_configurable_bounded_and_default_unchanged(self):
        self.assertEqual(3.0, self.client().settings.timeout_seconds)
        for timeout in (20, 30):
            self.assertEqual(timeout, self.client(timeout).settings.timeout_seconds)
            settings = llm.LLMSettings.from_env({
                "ACADEMIC_LLM_PROVIDER": "ollama", "ACADEMIC_LLM_BASE_URL": "http://127.0.0.1:11434",
                "ACADEMIC_LLM_MODEL": "test-model", "ACADEMIC_LLM_TIMEOUT_SECONDS": str(timeout),
            })
            self.assertEqual(timeout, settings.timeout_seconds)
        for timeout in (0, -1, 30.001, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.client(timeout)

    def test_longer_timeout_preserves_interval_from_inference_completion(self):
        a, b = self.client(20), self.client(20)
        def cold(*_):
            self.now += 11.516
            return "course.one"
        with patch.object(a, "_request_intent", side_effect=cold), patch.object(b, "_request_intent", return_value=None) as other:
            self.assertEqual("course.one", a.suggest_intent("course", {"course.one": "x"}))
            self.now += 4.999
            self.assertIsNone(b.suggest_intent("credits", {"course.one": "x"}))
            other.assert_not_called()
            self.now += .001
            b.suggest_intent("credits", {"course.one": "x"})
            other.assert_called_once()

    def test_longer_timeout_failure_still_has_sixty_second_cooldown(self):
        a, b = self.client(20), self.client(20)
        def timeout(*_):
            self.now += 20
            raise llm.LLMUnavailable("bounded cold-start timeout")
        with patch.object(a, "_request_intent", side_effect=timeout), patch.object(b, "_request_intent", return_value=None) as other:
            with self.assertRaises(llm.LLMUnavailable):
                a.suggest_intent("course", {"course.one": "x"})
            self.now += 59
            self.assertIsNone(b.suggest_intent("course", {"course.one": "x"}))
            other.assert_not_called()
            self.now += 1
            b.suggest_intent("course", {"course.one": "x"})
            other.assert_called_once()


if __name__ == "__main__": unittest.main()
