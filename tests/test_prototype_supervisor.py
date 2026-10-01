from __future__ import annotations

from contextlib import contextmanager, redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "operations"))
import prototype_supervisor as supervisor

CONFIG = {
    "ACADEMIC_EVIDENCE_BACKEND": "neo4j", "NEO4J_URI": "bolt://127.0.0.1:7687",
    "NEO4J_DATABASE": "neo4j", "NEO4J_USER": "own-account", "NEO4J_PASSWORD": "private-test-value",
    "ACADEMIC_LLM_PROVIDER": "disabled",
}
RUNTIME = {
    "evidence_backend": "neo4j", "graph_verified": True,
    "llm_configured": True, "llm_model_available": False,
    "ignored_secret": "do-not-emit-this-value",
}
READY = {
    "web": "available", "readiness": "ready", "graph": "verified",
    "optional_llm": "unavailable", "answer_mode": "rule_answers",
}


class Clock:
    def __init__(self):
        self.now = 0.0
        self.stopped = False
        self.waits = []
        self.stop_after_wait = None

    def monotonic(self):
        return self.now

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        if self.stop_after_wait is not None and len(self.waits) >= self.stop_after_wait:
            self.set()
        return self.stopped


def fake_child(*, exited=False):
    child = Mock()
    child.process.pid = 12345
    child.process.poll.return_value = 1 if exited else None
    child.stop.return_value = True
    return child


@contextmanager
def local_diagnostic_server():
    responses = {"/readyz": (200, {"status": "ready"}), "/v1/academic/runtime": (200, dict(RUNTIME))}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(("GET", self.path))
            status, payload = responses.get(self.path, (404, {}))
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/redirect-target")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    assert server.server_port != 8000
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(supervisor, "BASE_URL", f"http://127.0.0.1:{server.server_port}"):
            yield responses, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def trickling_diagnostic_server(stage):
    stopped = threading.Event()
    body = b'{"status":"ready"}'
    headers = (f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n"
               "Content-Type: application/json\r\nConnection: close\r\n\r\n").encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if stage == "body":
                    self.connection.sendall(headers)
                slow = headers if stage == "headers" else body
                for value in slow:
                    if stopped.wait(0.02):
                        return
                    self.connection.sendall(bytes([value]))
                if stage == "headers":
                    self.connection.sendall(body)
            except OSError:
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    assert server.server_port != 8000
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        with patch.object(supervisor, "BASE_URL", f"http://127.0.0.1:{server.server_port}"):
            yield
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class PrototypeSupervisorTests(unittest.TestCase):
    def test_local_config_is_validated_without_inherited_credentials(self):
        inherited = {
            "NEO4J_PASSWORD": "stale-secret", "ACADEMIC_LLM_API_KEY": "stale-key",
            "ACADEMIC_FEEDBACK_PATH": "stale-path", "ACADEMIC_EVIDENCE_BACKEND": "registry",
            "PYTHONPATH": "stale-python-path", "PATH": os.environ.get("PATH", ""),
        }
        with patch.dict(os.environ, inherited, clear=True), patch.object(supervisor, "load_private_settings", return_value=CONFIG) as load:
            before = dict(os.environ)
            environment = supervisor.private_environment(ROOT / ".local" / "test.json")
            self.assertEqual(before, dict(os.environ))
        load.assert_called_once_with(ROOT / ".local" / "test.json")
        self.assertEqual("private-test-value", environment["NEO4J_PASSWORD"])
        self.assertEqual("neo4j", environment["ACADEMIC_EVIDENCE_BACKEND"])
        self.assertNotIn("ACADEMIC_LLM_API_KEY", environment)
        self.assertNotIn("ACADEMIC_FEEDBACK_PATH", environment)
        self.assertNotIn("stale-python-path", environment["PYTHONPATH"])
        self.assertIn(str(ROOT / "src"), environment["PYTHONPATH"])
        if os.name == "nt" and sys.prefix != sys.base_prefix:
            self.assertEqual(sys.prefix, environment["VIRTUAL_ENV"])
            self.assertIn(str(Path(sys.prefix) / "Lib" / "site-packages"), environment["PYTHONPATH"])

    def test_registry_or_missing_graph_settings_cannot_launch(self):
        for config in ({}, {"ACADEMIC_EVIDENCE_BACKEND": "registry"}, {"ACADEMIC_EVIDENCE_BACKEND": "neo4j"}):
            with self.subTest(config=config), patch.object(supervisor, "load_private_settings", return_value=config):
                with self.assertRaises(ValueError):
                    supervisor.private_environment(ROOT / ".local" / "test.json")

    @unittest.skipUnless(os.name == "nt", "Windows base runtime dependency boundary")
    def test_direct_windows_runtime_has_project_dependencies_without_venv_launcher(self):
        with patch.object(supervisor, "load_private_settings", return_value=CONFIG):
            environment = supervisor.private_environment(ROOT / ".local" / "test.json")
        result = subprocess.run(
            [sys._base_executable, "-c", "import fastapi,uvicorn,neo4j; import academic_assistant.api; print('DEPENDENCIES_OK')"],
            env=environment, cwd=ROOT, capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(0, result.returncode, "direct runtime could not import project dependencies")
        self.assertEqual(b"DEPENDENCIES_OK", result.stdout.strip())

    def test_configuration_failure_is_sanitized_and_does_not_spawn(self):
        output = io.StringIO()
        with patch.object(supervisor, "private_environment", side_effect=ValueError("secret-url-password")), patch.object(supervisor, "spawn_web") as spawn, redirect_stdout(output):
            code = supervisor.main([])
        self.assertEqual(supervisor.EXIT_CONFIG, code)
        self.assertEqual({"event": "configuration_unavailable"}, json.loads(output.getvalue()))
        spawn.assert_not_called()

    def test_check_once_only_gets_diagnostics_and_never_loads_config(self):
        with local_diagnostic_server() as (_responses, requests):
            output = io.StringIO()
            with patch.object(supervisor, "private_environment") as config, patch.object(supervisor, "spawn_web") as spawn, patch.object(supervisor, "port_available") as bind, redirect_stdout(output):
                code = supervisor.main(["--check-once", "--config", "nonexistent.json"])
        self.assertEqual(supervisor.EXIT_OK, code)
        self.assertEqual([("GET", "/readyz"), ("GET", "/v1/academic/runtime")], requests)
        self.assertEqual({"event": "diagnostic", **READY}, json.loads(output.getvalue()))
        self.assertNotIn("do-not-emit", output.getvalue())
        config.assert_not_called()
        spawn.assert_not_called()
        bind.assert_not_called()

    def test_graph_outage_503_and_recovery_are_observed_read_only(self):
        with local_diagnostic_server() as (responses, _requests):
            for path in responses:
                responses[path] = (503, {"detail": "secret endpoint error"})
            lost = supervisor.diagnostic(timeout=1)
            self.assertEqual("available", lost["web"])
            self.assertEqual("unavailable", lost["readiness"])
            self.assertEqual("unavailable", lost["graph"])
            responses["/readyz"] = (200, {"status": "ready"})
            responses["/v1/academic/runtime"] = (200, dict(RUNTIME))
            self.assertEqual(READY, supervisor.diagnostic(timeout=1))

    def test_runtime_registry_response_is_not_accepted_as_ready(self):
        with local_diagnostic_server() as (responses, _requests):
            responses["/v1/academic/runtime"] = (200, {**RUNTIME, "evidence_backend": "registry", "graph_verified": False})
            with redirect_stdout(io.StringIO()):
                self.assertEqual(supervisor.EXIT_NOT_READY, supervisor.main(["--check-once"]))

    def test_llm_inventory_is_optional_and_not_inference(self):
        with local_diagnostic_server() as (responses, requests):
            for configured, available, expected in ((True, True, "available"), (True, False, "unavailable"), (False, False, "disabled")):
                responses["/v1/academic/runtime"] = (200, {**RUNTIME, "llm_configured": configured, "llm_model_available": available})
                state = supervisor.diagnostic(timeout=1)
                self.assertEqual("ready", state["readiness"])
                self.assertEqual("rule_answers", state["answer_mode"])
                self.assertEqual(expected, state["optional_llm"])
            self.assertTrue(all(method == "GET" for method, _ in requests))
            self.assertTrue(all(path in {"/readyz", "/v1/academic/runtime"} for _, path in requests))

    def test_redirect_duplicate_json_and_large_diagnostics_fail_closed(self):
        with local_diagnostic_server() as (responses, requests):
            responses["/readyz"] = (302, {})
            self.assertEqual("unavailable", supervisor.diagnostic(timeout=1)["readiness"])
            self.assertNotIn(("GET", "/redirect-target"), requests)
            responses["/readyz"] = (200, b'{"status":"ready","status":"ready"}')
            self.assertEqual("unavailable", supervisor.diagnostic(timeout=1)["readiness"])
            responses["/readyz"] = (200, b" " * (supervisor.MAX_RESPONSE_BYTES + 1))
            self.assertEqual("unavailable", supervisor.diagnostic(timeout=1)["readiness"])

    def test_network_failure_has_no_error_text(self):
        with patch.object(supervisor.socket, "socket", side_effect=OSError("secret-url-password")):
            self.assertEqual((None, None), supervisor._get_json("/readyz", 1))

    def test_explicit_depth_64_is_accepted_and_65_is_rejected_before_decoder(self):
        self.assertEqual(64, supervisor.MAX_JSON_DEPTH)
        with local_diagnostic_server() as (responses, _requests):
            for opening, closing, kind in ((b"[", b"]", "array"), (b'{"x":', b"}", "object")):
                with self.subTest(kind=kind, depth=64):
                    body = b'{"nested":' + opening * 63 + b"0" + closing * 63 + b"}"
                    responses["/readyz"] = (200, body)
                    code, data = supervisor._get_json("/readyz", 0.2)
                    self.assertEqual(200, code)
                    value = data["nested"]
                    for _ in range(63):
                        value = value[0] if kind == "array" else value["x"]
                    self.assertEqual(0, value)
                with self.subTest(kind=kind, depth=65):
                    body = b'{"nested":' + opening * 64 + b"0" + closing * 64 + b"}"
                    responses["/readyz"] = (200, body)
                    with patch.object(supervisor.json, "loads", side_effect=AssertionError("over-depth body reached decoder")) as decoder:
                        self.assertEqual((None, None), supervisor._get_json("/readyz", 0.2))
                        decoder.assert_not_called()

    def test_depth_guard_ignores_brackets_and_escaped_quotes_inside_strings(self):
        marker = '학사 🎓 [{"quoted"}]' * 100 + '"' + "\\" * 3 + '"' + "\\"
        leaf = json.dumps(marker, ensure_ascii=False).encode("utf-8")
        body = b'{"nested":' + b"[" * 63 + leaf + b"]" * 63 + b"}"
        with local_diagnostic_server() as (responses, _requests):
            responses["/readyz"] = (200, body)
            code, data = supervisor._get_json("/readyz", 0.2)
            self.assertEqual(200, code)
            value = data["nested"]
            for _ in range(63):
                value = value[0]
            self.assertEqual(marker, value)

    def test_non_utf8_or_bom_diagnostic_json_is_rejected(self):
        text = '{"status":"ready"}'
        with local_diagnostic_server() as (responses, _requests):
            for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
                with self.subTest(encoding=encoding):
                    responses["/readyz"] = (200, text.encode(encoding))
                    self.assertEqual((None, None), supervisor._get_json("/readyz", 0.2))

    def test_malformed_json_still_returns_sanitized_unavailability(self):
        with local_diagnostic_server() as (responses, _requests):
            for body in (b'{"x":[0}', b'{"x":"unterminated}', b'{"x":}', b'{"x":"bad\\q"}'):
                with self.subTest(body=body):
                    responses["/readyz"] = (200, body)
                    self.assertEqual((None, None), supervisor._get_json("/readyz", 0.2))

    def test_deep_json_and_decoder_capacity_failures_are_sanitized(self):
        deep = b'{"x":' + b"[" * 4000 + b"0" + b"]" * 4000 + b"}"
        self.assertEqual(8007, len(deep))
        self.assertLessEqual(len(deep), supervisor.MAX_RESPONSE_BYTES)
        with local_diagnostic_server() as (responses, _requests):
            responses["/readyz"] = (200, deep)
            self.assertEqual((None, None), supervisor._get_json("/readyz", 0.2))
            responses["/readyz"] = (200, {"status": "ready"})
            for error in (RecursionError, MemoryError):
                with self.subTest(error=error), patch.object(supervisor.json, "loads", side_effect=error("private decoder detail")):
                    self.assertEqual((None, None), supervisor._get_json("/readyz", 0.2))

    def test_deep_json_does_not_abort_bounded_own_child_cleanup(self):
        deep = b'{"x":' + b"[" * 4000 + b"0" + b"]" * 4000 + b"}"
        child = fake_child()
        limits = supervisor.Limits(max_restarts=0, poll_seconds=0.1,
                                  shutdown_timeout_seconds=0.1, max_runtime_seconds=0.2)
        output = io.StringIO()
        with local_diagnostic_server() as (responses, _requests), patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", return_value=child) as spawn, patch.object(supervisor, "web_listening", return_value=True), redirect_stdout(output):
            responses["/readyz"] = (200, deep)
            started = time.monotonic()
            code = supervisor.supervise({}, limits, threading.Event())
            elapsed = time.monotonic() - started
        self.assertEqual(supervisor.EXIT_OK, code)
        self.assertLess(elapsed, 0.65)
        spawn.assert_called_once()
        child.stop.assert_called_once_with(0.1)
        self.assertNotIn("RecursionError", output.getvalue())
        states = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual("unavailable", next(state["readiness"] for state in states if state["event"] == "diagnostic"))

    def test_trickled_headers_and_body_have_total_deadline_and_close_owned_sockets(self):
        actual_socket = socket.socket
        for stage in ("headers", "body"):
            with self.subTest(stage=stage), socket.socket() as unrelated, trickling_diagnostic_server(stage):
                unrelated.bind(("127.0.0.1", 0))
                unrelated.listen(1)
                self.assertNotEqual(8000, unrelated.getsockname()[1])
                clients = []

                def counted_socket(*args, **kwargs):
                    connection = actual_socket(*args, **kwargs)
                    if "fileno" not in kwargs:  # Ignore the fixture's accepted sockets.
                        clients.append(connection)
                    return connection

                with patch.object(supervisor.socket, "socket", side_effect=counted_socket), patch.object(supervisor.threading, "Timer") as timer:
                    for _ in range(3):
                        started = time.monotonic()
                        code, data = supervisor._get_json("/readyz", 0.06)
                        elapsed = time.monotonic() - started
                        self.assertIsNone(data)
                        self.assertLess(elapsed, 0.25, f"{stage} escaped total deadline: {elapsed:.3f}s")
                    timer.assert_not_called()
                self.assertEqual(3, len(clients))
                self.assertTrue(all(connection.fileno() == -1 for connection in clients))
                self.assertNotEqual(-1, unrelated.fileno())
                with socket.create_connection(unrelated.getsockname(), timeout=1):
                    pass

    def test_supervisor_runtime_budget_includes_trickled_headers_and_body(self):
        for stage in ("headers", "body"):
            child = fake_child()
            limits = supervisor.Limits(max_restarts=0, poll_seconds=0.1,
                                      shutdown_timeout_seconds=0.1, max_runtime_seconds=0.2)
            with self.subTest(stage=stage), trickling_diagnostic_server(stage), patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", return_value=child), patch.object(supervisor, "web_listening", return_value=True), redirect_stdout(io.StringIO()):
                started = time.monotonic()
                code = supervisor.supervise({}, limits, threading.Event())
                elapsed = time.monotonic() - started
            self.assertEqual(supervisor.EXIT_OK, code)
            self.assertLess(elapsed, 0.65, f"{stage} escaped supervisor budget: {elapsed:.3f}s")
            child.stop.assert_called_once_with(0.1)

    def test_occupied_ephemeral_port_is_rejected_without_spawning(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            self.assertNotEqual(8000, listener.getsockname()[1])
            with patch.object(supervisor, "PORT", listener.getsockname()[1]), patch.object(supervisor, "spawn_web") as spawn, patch.object(supervisor, "diagnostic") as check, redirect_stdout(io.StringIO()):
                self.assertFalse(supervisor.port_available())
                code = supervisor.supervise({}, supervisor.Limits(max_runtime_seconds=1), Clock())
            self.assertEqual(supervisor.EXIT_OCCUPIED, code)
            spawn.assert_not_called()
            check.assert_not_called()
            with socket.create_connection(listener.getsockname(), timeout=1):
                pass  # Its unrelated owner remains alive.

    def test_restarts_have_finite_budget_and_capped_backoff(self):
        clock = Clock()
        children = [fake_child(exited=True) for _ in range(4)]
        output = io.StringIO()
        limits = supervisor.Limits(backoff_seconds=3, max_backoff_seconds=4, max_runtime_seconds=30)
        with patch.object(supervisor.time, "monotonic", clock.monotonic), patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", side_effect=children) as spawn, patch.object(supervisor, "diagnostic") as check, redirect_stdout(output):
            code = supervisor.supervise({}, limits, clock)
        self.assertEqual(supervisor.EXIT_RESTARTS, code)
        self.assertEqual(4, spawn.call_count)
        self.assertEqual([3, 4, 4], clock.waits)
        check.assert_not_called()
        for child in children:
            child.stop.assert_called_once_with(5)
        self.assertIn("restart_budget_exhausted", output.getvalue())

    def test_live_graph_loss_and_optional_llm_loss_do_not_restart_child(self):
        clock = Clock()
        child = fake_child()
        lost = {**READY, "readiness": "unavailable", "graph": "unavailable", "optional_llm": "unknown", "answer_mode": "unavailable"}
        output = io.StringIO()
        with patch.object(supervisor.time, "monotonic", clock.monotonic), patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", return_value=child) as spawn, patch.object(supervisor, "diagnostic", side_effect=[READY, lost, READY]), redirect_stdout(output):
            code = supervisor.supervise({}, supervisor.Limits(poll_seconds=1, max_runtime_seconds=3), clock)
        self.assertEqual(supervisor.EXIT_OK, code)
        spawn.assert_called_once()
        child.stop.assert_called_once_with(5)
        states = [json.loads(line) for line in output.getvalue().splitlines() if json.loads(line)["event"] == "diagnostic"]
        self.assertEqual(["ready", "unavailable", "ready"], [state["readiness"] for state in states])

    def test_startup_without_http_response_is_bounded_and_own_child_stopped(self):
        clock = Clock()
        child = fake_child()
        unavailable = {**READY, "web": "unavailable", "readiness": "unavailable", "graph": "unavailable"}
        with patch.object(supervisor.time, "monotonic", clock.monotonic), patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", return_value=child), patch.object(supervisor, "diagnostic", return_value=unavailable), patch.object(supervisor, "web_listening", return_value=False), redirect_stdout(io.StringIO()):
            code = supervisor.supervise({}, supervisor.Limits(max_restarts=0, poll_seconds=1, startup_timeout_seconds=1, max_runtime_seconds=5), clock)
        self.assertEqual(supervisor.EXIT_RESTARTS, code)
        self.assertEqual(1, clock.now)
        child.stop.assert_called_once()

    def test_slow_graph_checks_during_startup_cannot_restart_listening_child(self):
        clock = Clock()
        child = fake_child()
        unavailable = {**READY, "web": "unavailable", "readiness": "unavailable", "graph": "unavailable"}
        with patch.object(supervisor.time, "monotonic", clock.monotonic), patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", return_value=child) as spawn, patch.object(supervisor, "diagnostic", return_value=unavailable), patch.object(supervisor, "web_listening", return_value=True), redirect_stdout(io.StringIO()):
            code = supervisor.supervise({}, supervisor.Limits(max_restarts=0, poll_seconds=1, startup_timeout_seconds=1, max_runtime_seconds=3), clock)
        self.assertEqual(supervisor.EXIT_OK, code)
        spawn.assert_called_once()
        child.stop.assert_called_once()

    def test_new_port_owner_during_restart_is_not_killed(self):
        clock = Clock()
        child = fake_child(exited=True)
        with patch.object(supervisor.time, "monotonic", clock.monotonic), patch.object(supervisor, "port_available", side_effect=[True, False]), patch.object(supervisor, "spawn_web", return_value=child) as spawn, redirect_stdout(io.StringIO()):
            code = supervisor.supervise({}, supervisor.Limits(max_runtime_seconds=5), clock)
        self.assertEqual(supervisor.EXIT_OCCUPIED, code)
        spawn.assert_called_once()
        child.stop.assert_called_once()

    def test_signal_during_backoff_stops_without_new_child(self):
        clock = Clock()
        clock.stop_after_wait = 1
        child = fake_child(exited=True)
        with patch.object(supervisor.time, "monotonic", clock.monotonic), patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", return_value=child) as spawn, redirect_stdout(io.StringIO()):
            code = supervisor.supervise({}, supervisor.Limits(max_runtime_seconds=10), clock)
        self.assertEqual(supervisor.EXIT_OK, code)
        spawn.assert_called_once()
        child.stop.assert_called_once()

    def test_shutdown_failure_is_reported_as_failure(self):
        clock = Clock()
        clock.stop_after_wait = 1
        child = fake_child()
        child.stop.return_value = False
        with patch.object(supervisor.time, "monotonic", clock.monotonic), patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", return_value=child), patch.object(supervisor, "diagnostic", return_value=READY), redirect_stdout(io.StringIO()):
            self.assertEqual(supervisor.EXIT_SHUTDOWN, supervisor.supervise({}, supervisor.Limits(max_runtime_seconds=5), clock))

    def test_unexpected_probe_failure_still_shuts_down_owned_child(self):
        child = fake_child()
        with patch.object(supervisor, "port_available", return_value=True), patch.object(supervisor, "spawn_web", return_value=child), patch.object(supervisor, "diagnostic", side_effect=RuntimeError("test probe failure")), redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                supervisor.supervise({}, supervisor.Limits(max_runtime_seconds=1), Clock())
        child.stop.assert_called_once()

    def test_nonfinite_or_unbounded_cli_options_are_rejected_before_actions(self):
        for option, value in (("--max-restarts", "4"), ("--max-runtime-seconds", "3601"), ("--max-runtime-seconds", "nan"), ("--backoff-seconds", "inf"), ("--max-backoff-seconds", "11"), ("--port", "8080")):
            with self.subTest(option=option, value=value), patch.object(supervisor, "private_environment") as config, patch.object(supervisor, "diagnostic") as check, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    supervisor.main([option, value])
                self.assertEqual(2, error.exception.code)
                config.assert_not_called()
                check.assert_not_called()

    def test_spawn_uses_fixed_trusted_command_and_discards_child_output(self):
        process = Mock()
        process.pid = 12345
        with patch.object(supervisor.subprocess, "Popen", return_value=process) as popen, patch.object(supervisor, "_WindowsJob") as job:
            child = supervisor.spawn_web({"only": "test"}, 1)
        call = popen.call_args
        expected = sys._base_executable if os.name == "nt" else sys.executable
        self.assertEqual(expected, call.args[0][0])
        self.assertEqual("--web-child", call.args[0][-1])
        self.assertEqual(subprocess.DEVNULL, call.kwargs["stdout"])
        self.assertEqual(subprocess.DEVNULL, call.kwargs["stderr"])
        self.assertNotIn("shell", call.kwargs)
        process.stdin.write.assert_called_once_with(b"1")
        if os.name == "nt":
            job.return_value.assign.assert_called_once_with(process)
            self.assertIs(child.job, job.return_value)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree boundary")
    def test_owned_windows_job_closes_descendant_ephemeral_listener(self):
        descendant_code = "import socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(2); print(s.getsockname()[1],flush=True); time.sleep(60)"
        parent_code = (
            "import subprocess,sys,time; sys.stdin.buffer.read(1); "
            f"child=subprocess.Popen([sys.executable,'-c',{descendant_code!r}],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL); "
            "print(child.stdout.readline().decode().strip(),flush=True); time.sleep(60)"
        )
        for crash in (False, True):
            with self.subTest(crash=crash):
                job = supervisor._WindowsJob()
                process = subprocess.Popen([sys._base_executable, "-c", parent_code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                owned = supervisor.OwnedChild(process, job)
                try:
                    job.assign(process)
                    process.stdin.write(b"1")
                    process.stdin.close()
                    output = queue.Queue()
                    thread = threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True)
                    thread.start()
                    port = int(output.get(timeout=5))
                    self.assertNotEqual(8000, port)
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        pass
                    if crash:
                        process.kill()  # Exact Popen handle of this test's own root.
                        process.wait(timeout=2)
                    self.assertTrue(owned.stop(1))
                    deadline = time.monotonic() + 2
                    while True:
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                                pass
                        except OSError:
                            break
                        self.assertLess(time.monotonic(), deadline, "owned descendant listener survived job close")
                        time.sleep(0.02)
                    self.assertIsNotNone(process.poll())
                    thread.join(timeout=1)
                finally:
                    owned.stop(1)
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
