from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "operations"))
import public_demo as demo

URL = "https://approved-test-link.trycloudflare.com"


class Clock:
    def __init__(self):
        self.now, self.stopped = 0.0, False
        self.waits = []
        self.stop_after_wait = None
        self.on_wait = None

    def monotonic(self):
        return self.now

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.now += seconds
        self.waits.append(seconds)
        if self.on_wait is not None:
            self.on_wait()
        if self.stop_after_wait is not None and len(self.waits) >= self.stop_after_wait:
            self.set()
        return self.stopped


def child(pid, *, exited=False):
    result = Mock()
    result.process.pid = pid
    result.process.poll.return_value = 1 if exited else None
    result.stop.return_value = True
    return result


@contextmanager
def private_files():
    with tempfile.TemporaryDirectory(prefix="public-demo-test-") as directory:
        files = demo.DemoFiles(Path(directory))
        files.local.mkdir()
        (files.local / "tools").mkdir()
        yield files


class PublicDemoLauncherTests(unittest.TestCase):
    def test_verified_binary_rejects_missing_or_altered_bytes_without_spawning(self):
        with private_files() as files, patch.object(demo, "_spawn") as spawn:
            with self.assertRaises(FileNotFoundError):
                demo.spawn_tunnel(files, 1)
            trusted = b"test-only-trusted-binary"
            files.binary.write_bytes(trusted)
            with patch.object(demo, "CLOUDFLARED_SHA256", hashlib.sha256(trusted).hexdigest()):
                demo.verify_binary(files)
                files.binary.write_bytes(trusted + b"changed")
                with self.assertRaises(ValueError):
                    demo.spawn_tunnel(files, 1)
            spawn.assert_not_called()
            self.assertFalse(files.config.exists())

    def test_empty_explicit_tunnel_config_never_overwrites_personal_settings(self):
        with private_files() as files:
            demo.prepare_tunnel_config(files)
            self.assertEqual(b"{}\n", files.config.read_bytes())
            demo.prepare_tunnel_config(files)
            personal = b"credentials-file: secret-file.json\n"
            files.config.write_bytes(personal)
            with self.assertRaises(ValueError):
                demo.prepare_tunnel_config(files)
            self.assertEqual(personal, files.config.read_bytes())

    def test_storage_must_be_project_local_and_git_ignored(self):
        with private_files() as files, patch.object(demo.subprocess, "run", return_value=Mock(returncode=0)) as check:
            demo.DemoFiles(files.root.resolve()).validate()
            self.assertEqual(7, check.call_count)
            check.return_value.returncode = 1
            with self.assertRaises(ValueError):
                files.validate()
            self.assertFalse(files.state.exists())

    def test_public_environment_has_own_interval_without_mutating_private_settings(self):
        for interval, expected in (("5", "60.0"), ("120", "120.0")):
            private = {"ACADEMIC_LLM_MIN_INTERVAL_SECONDS": interval, "NEO4J_PASSWORD": "private-value"}
            with self.subTest(interval=interval), patch.object(demo, "private_environment", return_value=private):
                environment = demo.demo_environment(Path("private.json"))
            self.assertEqual(interval, private["ACADEMIC_LLM_MIN_INTERVAL_SECONDS"])
            self.assertNotIn("ACADEMIC_PUBLIC_DEMO", private)
            self.assertEqual(expected, environment["ACADEMIC_LLM_MIN_INTERVAL_SECONDS"])
            self.assertEqual("1", environment["ACADEMIC_PUBLIC_DEMO"])
        for interval in ("nan", "inf", "601", "-1"):
            with self.subTest(interval=interval), patch.object(demo, "private_environment", return_value={"ACADEMIC_LLM_MIN_INTERVAL_SECONDS": interval}):
                with self.assertRaises(ValueError):
                    demo.demo_environment(Path("private.json"))

    def test_cloudflared_does_not_inherit_service_credentials_or_tunnel_options(self):
        inherited = {"TUNNEL_TOKEN": "secret", "tunnel_config": "secret", "CF_API_KEY": "secret",
                     "cf_access_token": "secret", "NEO4J_PASSWORD": "secret",
                     "ACADEMIC_LLM_API_KEY": "secret", "PATH": "safe-path"}
        with patch.dict(os.environ, inherited, clear=True):
            before = dict(os.environ)
            self.assertEqual({"PATH": "safe-path"}, demo.tunnel_environment())
            self.assertEqual(before, dict(os.environ))

    def test_tunnel_fixed_command_uses_only_explicit_empty_config_loopback_and_bounded_retries(self):
        with private_files() as files, patch.object(demo, "verify_binary") as verify, patch.object(demo, "_spawn") as spawn:
            demo.spawn_tunnel(files, 1)
        verify.assert_called_once_with(files)
        wrapper, environment, timeout = spawn.call_args.args
        self.assertEqual("--tunnel-child", wrapper[-1])
        command = demo.tunnel_command(files)
        self.assertEqual([str(files.binary), "tunnel", "--config", str(files.config),
                          "--no-autoupdate", "--url", "http://127.0.0.1:8765", "--metrics", "127.0.0.1:0",
                          "--protocol", "http2", "--retries", "2"], command)
        self.assertEqual(1, timeout)
        self.assertEqual({"tunnel": True}, spawn.call_args.kwargs)
        self.assertFalse(any(key.upper().startswith(("CF_", "TUNNEL_")) for key in environment))

    def test_web_spawn_assigns_owned_job_before_releasing_trusted_child(self):
        process = Mock()
        order = []
        process.stdin.write.side_effect = lambda _data: order.append("gate")
        with patch.object(demo.subprocess, "Popen", return_value=process) as popen, patch.object(demo, "_WindowsJob") as job:
            job.return_value.assign.side_effect = lambda _child: order.append("assign")
            owned = demo.spawn_web({"ACADEMIC_PUBLIC_DEMO": "1"}, 1)
        command = popen.call_args.args[0]
        self.assertEqual("--web-child", command[-1])
        self.assertEqual(sys._base_executable if os.name == "nt" else sys.executable, command[0])
        self.assertEqual(subprocess.DEVNULL, popen.call_args.kwargs["stdout"])
        self.assertEqual(subprocess.DEVNULL, popen.call_args.kwargs["stderr"])
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertIs(process, owned.process)
        if os.name == "nt":
            self.assertEqual(["assign", "gate"], order)
        process.stdin.write.assert_called_once_with(b"1")
        process.stdin.flush.assert_called_once()
        process.stdin.close.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows base-runtime dependency boundary")
    def test_actual_web_bootstrap_imports_pinned_neo4j_without_bundled_native_packages(self):
        config = {"ACADEMIC_EVIDENCE_BACKEND": "neo4j", "NEO4J_URI": "bolt://127.0.0.1:7687",
                  "NEO4J_DATABASE": "neo4j", "NEO4J_USER": "synthetic-user",
                  "NEO4J_PASSWORD": "synthetic-secret", "ACADEMIC_LLM_PROVIDER": "disabled"}
        provider_module = sys.modules[demo.private_environment.__module__]
        with patch.object(provider_module, "load_private_settings", return_value=config):
            environment = demo.demo_environment(Path("synthetic-not-read.json"))
        with patch.object(demo, "_spawn", return_value=Mock()) as spawn:
            demo.spawn_web(environment, 3)
        command = spawn.call_args.args[0]
        self.assertEqual(sys._base_executable, command[0])
        self.assertEqual(["-S"], command[1:-2])
        workload = (
            "import sys\nassert sys.stdin.buffer.read(1)==b'1'\n"
            "import importlib.metadata,importlib.util,json\n"
            "assert sys.flags.no_site==1\n"
            "assert importlib.util.find_spec('numpy') is None\n"
            "import neo4j,fastapi,pydantic\n"
            "from pathlib import Path\n"
            "from academic_assistant import api\n"
            f"packages=Path({str(ROOT / '.venv' / 'Lib' / 'site-packages')!r}).resolve()\n"
            "assert Path(neo4j.__file__).resolve().is_relative_to(packages)\n"
            "assert Path(pydantic.__file__).resolve().is_relative_to(packages)\n"
            "assert importlib.metadata.version('neo4j')=='6.3.1'\n"
            "assert importlib.metadata.version('pydantic')=='2.11.9'\n"
            "assert importlib.metadata.version('fastapi')=='0.116.1'\n"
            "print(json.dumps({'no_site':sys.flags.no_site,'neo4j':neo4j.__version__,'numpy':False}),flush=True)\n"
            "assert sys.stdin.buffer.read(1)==b'0'\n"
        )
        owned = None
        started = time.monotonic()
        try:
            # Real OS ownership/gate and configured base-runtime options; the
            # diagnostic imports only and opens no graph/model/listener socket.
            owned = demo._spawn([command[0], *command[1:-2], "-c", workload], environment, 3, tunnel=True)
            output, _ = owned.process.communicate(input=b"0", timeout=5)
            self.assertEqual(0, owned.process.returncode)
            self.assertLess(len(output), 1024)
            self.assertEqual({"no_site": 1, "neo4j": "6.3.1", "numpy": False}, json.loads(output))
            self.assertLess(time.monotonic() - started, 5)
        finally:
            if owned is not None:
                self.assertTrue(owned.stop(3))
                if owned.process.stdout is not None:
                    owned.process.stdout.close()
    def test_controlled_child_requests_graceful_close_before_owned_fallback(self):
        for timed_out in (False, True):
            process, job = Mock(), Mock()
            process.poll.return_value = None
            process.wait.side_effect = subprocess.TimeoutExpired("trusted", 1) if timed_out else None
            with self.subTest(timed_out=timed_out), patch.object(demo.OwnedChild, "stop", return_value=True) as fallback:
                self.assertTrue(demo.ControlledChild(process, job).stop(1))
            process.stdin.write.assert_called_once_with(b"0")
            process.stdin.flush.assert_called_once()
            process.stdin.close.assert_called_once()
            process.wait.assert_called_once_with(timeout=1)
            fallback.assert_called_once()
            self.assertGreaterEqual(fallback.call_args.args[0], 0.1)

    def test_tunnel_wrapper_rechecks_hash_and_uses_hidden_owned_console_without_shell(self):
        with private_files() as files, patch.object(demo, "DemoFiles", return_value=files), patch.object(type(files), "validate"), patch.object(demo, "verify_binary") as verify, patch.object(demo.sys, "stdin", Mock(buffer=io.BytesIO(b"10"))), patch.object(demo.subprocess, "Popen") as popen:
            popen.return_value.poll.return_value = 0
            popen.return_value.returncode = 0
            self.assertEqual(demo.EXIT_OK, demo._tunnel_child())
            verify.assert_called_once_with(files)
            self.assertEqual(demo.tunnel_command(files), popen.call_args.args[0])
            self.assertNotIn("shell", popen.call_args.kwargs)
            self.assertNotIn("stdout", popen.call_args.kwargs)
            if os.name == "nt":
                self.assertEqual(subprocess.CREATE_NEW_CONSOLE, popen.call_args.kwargs["creationflags"])
                self.assertEqual(0, popen.call_args.kwargs["startupinfo"].wShowWindow)
            popen.return_value.terminate.assert_not_called()

    def test_tunnel_wrapper_refuses_bad_hash_before_creating_child(self):
        with private_files() as files, patch.object(demo, "DemoFiles", return_value=files), patch.object(type(files), "validate"), patch.object(demo, "verify_binary", side_effect=ValueError("bad binary")), patch.object(demo.sys, "stdin", Mock(buffer=io.BytesIO(b"1"))), patch.object(demo.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                demo._tunnel_child()
            popen.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows graceful console boundary")
    def test_graceful_console_signal_targets_only_owned_child_and_detaches(self):
        import ctypes
        kernel, process = Mock(), Mock(pid=12345)
        kernel.AttachConsole.return_value = True
        kernel.SetConsoleCtrlHandler.return_value = True
        kernel.GenerateConsoleCtrlEvent.return_value = True
        with patch.object(ctypes, "WinDLL", return_value=kernel):
            self.assertTrue(demo._interrupt_owned_console(process))
        kernel.AttachConsole.assert_called_once_with(12345)
        kernel.GenerateConsoleCtrlEvent.assert_called_once_with(1, 0)
        kernel.FreeConsole.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)
        kernel.AttachConsole.reset_mock()
        kernel.FreeConsole.reset_mock()
        kernel.GenerateConsoleCtrlEvent.reset_mock()
        kernel.AttachConsole.return_value = False
        with patch.object(ctypes, "WinDLL", return_value=kernel):
            self.assertFalse(demo._interrupt_owned_console(process))
        kernel.GenerateConsoleCtrlEvent.assert_not_called()
        kernel.FreeConsole.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows ownership boundary")
    def test_failed_job_assignment_cleans_exact_owned_process_and_output(self):
        process = Mock()
        with patch.object(demo.subprocess, "Popen", return_value=process), patch.object(demo, "_WindowsJob") as job, patch.object(demo, "OwnedChild") as owned:
            job.return_value.assign.side_effect = OSError("private-detail")
            with self.assertRaisesRegex(OSError, "owned demo child unavailable"):
                demo._spawn(["trusted"], {}, 1, tunnel=True)
        owned.assert_called_once_with(process, job.return_value)
        owned.return_value.stop.assert_called_once_with(1)
        process.stdout.close.assert_called_once()

    def test_web_child_requires_gate_and_public_flag_and_uses_demo_factory_only(self):
        import uvicorn
        for gate, flag in ((b"0", "1"), (b"1", "")):
            with patch.object(demo.sys, "stdin", Mock(buffer=io.BytesIO(gate))), patch.dict(os.environ, {"ACADEMIC_PUBLIC_DEMO": flag}), patch.object(uvicorn, "Server") as run:
                self.assertEqual(demo.EXIT_CONFIG, demo._web_child())
                run.assert_not_called()
        with patch.object(demo.sys, "stdin", Mock(buffer=io.BytesIO(b"10"))), patch.dict(os.environ, {"ACADEMIC_PUBLIC_DEMO": "1"}), patch.object(uvicorn, "Config") as config, patch.object(uvicorn, "Server") as server:
            self.assertEqual(demo.EXIT_OK, demo._web_child())
        config.assert_called_once_with("academic_assistant.public_demo:create_demo_app", factory=True,
                                       host="127.0.0.1", port=8765, workers=1,
                                       access_log=False, log_level="critical")
        server.return_value.run.assert_called_once()

    def test_local_ready_accepts_only_exact_demo_marker_and_never_follows_redirects(self):
        connection = Mock()
        connection.connect_ex.return_value = 0
        connection.getsockopt.return_value = 0
        connection.send.side_effect = lambda view: len(view)
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        for status, body, expected in (
            (200, {"status": "ready", "public_demo": True}, True),
            (200, {"status": "ready"}, False),
            (200, {"status": "ready", "public_demo": 1}, False),
            (200, {"status": "ready", "public_demo": True, "secret": "value"}, False),
            (302, None, False), (503, None, False),
        ):
            with self.subTest(status=status, body=body), patch.object(demo.socket, "socket", return_value=connection), patch.object(demo, "_socket_ready"), patch.object(demo, "_read_json_response", return_value=(status, body)):
                self.assertEqual(expected, demo.local_ready(0.1))
        connection.connect_ex.assert_called_with(("127.0.0.1", 8765))
        self.assertIn(b"GET /readyz HTTP/1.1", bytes(connection.send.call_args.args[0]))
        with patch.object(demo.socket, "socket", side_effect=OSError("secret-host-password")):
            self.assertFalse(demo.local_ready(0.1))

    def test_tunnel_stream_retains_only_exact_url_and_bounds_raw_startup(self):
        rejected = [URL + ".evil", URL + "/private", URL + "?token=secret",
                    "https://user@approved-test-link.trycloudflare.com", "prefix" + URL]
        for raw, expected, failed in (
            (("secret startup text\n| " + URL + " |\n").encode(), URL, False),
            (("\n".join(rejected) + "\n").encode(), None, False),
            ((URL + "\nhttps://different.trycloudflare.com\n").encode(), URL, True),
            (b"x" * (demo.MAX_LINE_BYTES + 1), None, True),
            (b"line\n" * (demo.MAX_STARTUP_LINES + 1), None, True),
        ):
            with self.subTest(expected=expected, failed=failed):
                output = demo.TunnelOutput(io.BytesIO(raw))
                output.thread.join(timeout=1)
                self.assertFalse(output.thread.is_alive())
                self.assertEqual(expected, output.url)
                self.assertEqual(failed, output.failed)
                output.close()

    def test_blocked_output_reader_reports_incomplete_cleanup_without_blocking_close(self):
        stream = Mock()
        with patch.object(demo.threading, "Thread") as thread:
            thread.return_value.is_alive.return_value = True
            output = demo.TunnelOutput(stream)
            self.assertFalse(output.close())
            thread.return_value.join.assert_called_once_with(timeout=1)
            stream.close.assert_not_called()

    def test_os_launch_lock_prevents_two_starts_without_pid_management(self):
        with private_files() as files:
            self.assertFalse(demo.launcher_active(files))
            with demo.LaunchLock(files.lock):
                self.assertTrue(demo.launcher_active(files))
                with self.assertRaises(BlockingIOError):
                    with demo.LaunchLock(files.lock):
                        self.fail("second launcher acquired lock")
            self.assertFalse(demo.launcher_active(files))

    def test_status_rejects_secret_or_malformed_state_and_does_not_expose_raw_values(self):
        base = {"phase": "running", "run_id": "a" * 32, "launcher_pid": 123, "url": URL}
        invalid = [{**base, "password": "secret"}, {**base, "url": URL + "?secret"},
                   {**base, "web_pid": True}, {**base, "phase": []}, {**base, "reason": []}]
        with private_files() as files:
            for state in invalid:
                with self.subTest(state=state):
                    files.state.write_text(json.dumps(state), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        demo.show_status(files)
            files.state.write_text('{"phase":"running","phase":"failed"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                demo.show_status(files)
            files.state.write_text('{"x":' + '[' * 100 + '0' + ']' * 100 + '}', encoding="utf-8")
            with self.assertRaises(ValueError):
                demo.show_status(files)

    def test_stop_request_is_idempotent_run_scoped_and_never_uses_recorded_pid_to_kill(self):
        with private_files() as files, patch.object(demo, "_spawn") as spawn, patch.object(demo.os, "kill") as kill, redirect_stdout(io.StringIO()):
            demo._save_state(files, {"phase": "running", "run_id": "b" * 32, "launcher_pid": 12345})
            self.assertEqual(demo.EXIT_OK, demo.request_stop(files))
            self.assertFalse(files.stop.exists())
            with demo.LaunchLock(files.identity_lock):
                pass
            with demo.LaunchLock(files.lock):
                self.assertEqual(demo.EXIT_OK, demo.request_stop(files))
                self.assertEqual(demo.EXIT_OK, demo.request_stop(files))
                self.assertTrue(demo._stop_requested(files, "b" * 32))
                self.assertFalse(demo._stop_requested(files, "c" * 32))
            spawn.assert_not_called()
            kill.assert_not_called()

    def test_stop_explicitly_retries_missing_or_terminal_state_under_new_active_lock(self):
        with private_files() as files, demo.LaunchLock(files.lock), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(demo.EXIT_LOCKED, demo.request_stop(files))
            for phase in ("stopped", "failed"):
                demo._save_state(files, {"phase": phase, "run_id": "a" * 32, "launcher_pid": 123})
                self.assertEqual(demo.EXIT_LOCKED, demo.request_stop(files))
                self.assertFalse(files.stop.exists())
        self.assertEqual([{"event": "public_demo_stop_retry"}] * 3,
                         [json.loads(line) for line in output.getvalue().splitlines()])

    def test_stop_retries_held_identity_guard_before_lifetime_lock_exists(self):
        with private_files() as files, redirect_stdout(io.StringIO()) as output:
            demo._save_state(files, {"phase": "running", "run_id": "a" * 32, "launcher_pid": 123})
            # Legacy stale state alone is truthfully inactive, even if it says running.
            self.assertFalse(files.identity_lock.exists())
            self.assertEqual(demo.EXIT_OK, demo.request_stop(files))
            with demo.LaunchLock(files.identity_lock):
                self.assertFalse(files.lock.exists())
                self.assertFalse(demo.launcher_active(files))
                self.assertEqual(demo.EXIT_LOCKED, demo.request_stop(files))
                self.assertFalse(files.stop.exists())
            self.assertEqual(demo.EXIT_OK, demo.request_stop(files))
        self.assertEqual([{"event": "public_demo_inactive"}, {"event": "public_demo_stop_retry"},
                          {"event": "public_demo_inactive"}],
                         [json.loads(line) for line in output.getvalue().splitlines()])

    def test_stale_running_identity_cannot_be_acknowledged_before_fresh_publication(self):
        with private_files() as files:
            previous = "a" * 32
            demo._save_state(files, {"phase": "running", "run_id": previous, "launcher_pid": 123})
            begin = demo._begin_run
            observed = {}

            def paused_identity(_files):
                self.assertTrue(demo.launcher_active(files))
                self.assertEqual(previous, demo._read_state(files)["run_id"])
                observed["initializing_stop"] = demo.request_stop(files)
                self.assertFalse(files.stop.exists())
                return begin(files)

            def fresh_preflight(_files):
                observed["fresh_id"] = demo._read_state(files)["run_id"]
                observed["fresh_stop"] = demo.request_stop(files)

            with patch.object(demo, "DemoFiles", return_value=files), patch.object(type(files), "validate"), \
                    patch.object(demo, "_begin_run", side_effect=paused_identity), \
                    patch.object(demo, "verify_binary", side_effect=fresh_preflight), \
                    patch.object(demo, "spawn_web") as web, patch.object(demo, "spawn_tunnel") as tunnel, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(demo.EXIT_OK, demo.main([]))
            self.assertEqual(demo.EXIT_LOCKED, observed["initializing_stop"])
            self.assertEqual(demo.EXIT_OK, observed["fresh_stop"])
            self.assertNotEqual(previous, observed["fresh_id"])
            self.assertEqual(observed["fresh_id"], demo._read_state(files)["run_id"])
            self.assertEqual("requested", demo._read_state(files)["reason"])
            web.assert_not_called()
            tunnel.assert_not_called()
            events = [json.loads(line)["event"] for line in output.getvalue().splitlines()]
            self.assertEqual(1, events.count("public_demo_stop_retry"))
            self.assertEqual(1, events.count("public_demo_stop_requested"))

    def test_acknowledged_stop_during_paused_preflight_preserves_new_identity_and_never_launches(self):
        entered, release = threading.Event(), threading.Event()
        observed, errors = {}, []
        with private_files() as files:
            previous = "a" * 32
            demo._save_state(files, {"phase": "stopped", "run_id": previous, "launcher_pid": 123})
            files.stop.write_bytes((previous + "\n").encode())

            def paused_preflight(_files):
                observed["preflight_state"] = demo._read_state(files)
                entered.set()
                self.assertTrue(release.wait(3), "Stop thread did not release preflight")

            def stop_during_preflight():
                try:
                    if not entered.wait(3):
                        raise AssertionError("preflight was not reached")
                    observed["stop_code"] = demo.request_stop(files)
                    observed["acknowledged_id"] = files.stop.read_text(encoding="ascii").strip()
                except BaseException as error:
                    errors.append(error)
                finally:
                    release.set()

            stopper = threading.Thread(target=stop_during_preflight)
            with patch.object(demo, "DemoFiles", return_value=files), patch.object(type(files), "validate"), \
                    patch.object(demo, "verify_binary", side_effect=paused_preflight), \
                    patch.object(demo, "prepare_tunnel_config") as prepare, \
                    patch.object(demo, "demo_environment") as environment, \
                    patch.object(demo, "spawn_web") as web, patch.object(demo, "spawn_tunnel") as tunnel, \
                    redirect_stdout(io.StringIO()) as output:
                stopper.start()
                try:
                    code = demo.main([])
                finally:
                    release.set()
                    stopper.join(timeout=3)
            self.assertFalse(stopper.is_alive())
            self.assertEqual([], errors)
            self.assertEqual(demo.EXIT_OK, observed["stop_code"])
            self.assertEqual(demo.EXIT_OK, code)
            self.assertNotEqual(previous, observed["preflight_state"]["run_id"])
            self.assertEqual(observed["preflight_state"]["run_id"], observed["acknowledged_id"])
            self.assertEqual("starting", observed["preflight_state"]["phase"])
            self.assertEqual("requested", demo._read_state(files)["reason"])
            self.assertEqual("stopped", demo._read_state(files)["phase"])
            self.assertFalse(files.stop.exists())
            prepare.assert_not_called()
            environment.assert_not_called()
            web.assert_not_called()
            tunnel.assert_not_called()
            self.assertTrue(any(json.loads(line)["event"] == "public_demo_stop_requested"
                                for line in output.getvalue().splitlines()))

    def run_launch(self, files, clock, *, ready=True, url=URL, limits=None, web=None, tunnel=None, output_failed=False):
        web, tunnel = web or child(100), tunnel or child(200)
        stream = Mock(url=url, failed=output_failed)
        order = []
        web.stop.side_effect = lambda _timeout: order.append("web") or True
        tunnel.stop.side_effect = lambda _timeout: order.append("tunnel") or True
        output = io.StringIO()
        with patch.object(demo.time, "monotonic", clock.monotonic), patch.object(demo, "port_available", return_value=True), patch.object(demo, "spawn_web", return_value=web) as web_spawn, patch.object(demo, "spawn_tunnel", return_value=tunnel) as tunnel_spawn, patch.object(demo, "local_ready", side_effect=ready if callable(ready) else None, return_value=ready) as readiness, patch.object(demo, "TunnelOutput", return_value=stream), redirect_stdout(output):
            code = demo.launch({}, files, limits or demo.Limits(max_runtime_seconds=0.75), clock)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        return code, events, order, web_spawn, tunnel_spawn, readiness

    def test_startup_announces_only_locally_verified_url_and_shuts_tunnel_first(self):
        with private_files() as files:
            code, events, order, web, tunnel, ready = self.run_launch(files, Clock())
            self.assertEqual(demo.EXIT_OK, code)
            self.assertEqual(["tunnel", "web"], order)
            web.assert_called_once()
            tunnel.assert_called_once()
            self.assertEqual(2, ready.call_count)
            event = next(event for event in events if event["event"] == "public_demo_ready")
            self.assertEqual(URL, event["url"])
            self.assertIs(event["local_ready"], True)
            self.assertIs(event["remote_verified"], False)
            self.assertEqual("cloudflare", events[0]["traffic_via"])
            self.assertEqual("runtime_limit", demo._read_state(files)["reason"])

    def test_default_runtime_runs_until_explicit_signal(self):
        clock = Clock()
        clock.stop_after_wait = 3
        with private_files() as files:
            code, events, order, *_ = self.run_launch(files, clock, limits=demo.Limits())
            self.assertEqual(demo.EXIT_OK, code)
            self.assertEqual("signal", events[-1]["reason"])
            self.assertEqual(["tunnel", "web"], order)
            self.assertIsNone(demo.Limits().max_runtime_seconds)

    def test_owned_stop_flag_is_consumed_and_stale_flag_does_not_stop_next_run(self):
        with private_files() as files:
            files.stop.write_bytes(b"stale-run-id\n")
            clock = Clock()
            clock.on_wait = lambda: files.stop.write_bytes((demo._read_state(files)["run_id"] + "\n").encode("ascii"))
            code, events, order, *_ = self.run_launch(files, clock)
            self.assertEqual(demo.EXIT_OK, code)
            self.assertEqual("requested", events[-1]["reason"])
            self.assertEqual(["tunnel", "web"], order)
            self.assertFalse(files.stop.exists())

    def test_readiness_timeout_never_starts_tunnel_and_does_not_restart_child(self):
        clock = Clock()
        with private_files() as files:
            code, events, order, web, tunnel, *_ = self.run_launch(files, clock, ready=False, limits=demo.Limits(startup_timeout_seconds=0.5))
            self.assertEqual(demo.EXIT_NOT_READY, code)
            self.assertEqual(0.5, clock.now)
            self.assertEqual(["web"], order)
            web.assert_called_once()
            tunnel.assert_not_called()
            self.assertEqual("startup_timeout", events[-1]["reason"])

    def test_tunnel_url_timeout_or_oversized_output_cleans_children_in_order(self):
        for output_failed, reason in ((False, "startup_timeout"), (True, "startup_output_unavailable")):
            with self.subTest(output_failed=output_failed), private_files() as files:
                code, events, order, *_ = self.run_launch(files, Clock(), url=None, output_failed=output_failed, limits=demo.Limits(startup_timeout_seconds=0.5))
                self.assertEqual(demo.EXIT_NOT_READY, code)
                self.assertEqual(["tunnel", "web"], order)
                self.assertEqual(reason, events[-1]["reason"])
                self.assertFalse(any(event["event"] == "public_demo_ready" for event in events))

    def test_slow_readiness_cannot_start_tunnel_after_total_startup_or_runtime_deadline(self):
        for runtime, expected in ((None, demo.EXIT_NOT_READY), (0.4, demo.EXIT_OK)):
            clock = Clock()
            def slow_ready(_timeout):
                clock.now += 0.6
                return True
            with self.subTest(runtime=runtime), private_files() as files:
                code, _events, order, _web, tunnel, *_ = self.run_launch(files, clock, ready=slow_ready, limits=demo.Limits(startup_timeout_seconds=0.5, max_runtime_seconds=runtime))
                self.assertEqual(expected, code)
                self.assertEqual(["web"], order)
                tunnel.assert_not_called()

    def test_actual_local_600ms_ready_response_can_start_within_overall_deadline(self):
        requests, events = [], []
        stop = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                time.sleep(0.6)
                body = b'{"status":"ready","public_demo":true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        def record(event, **fields):
            events.append({"event": event, **fields})
            if event == "public_demo_ready":
                stop.set()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        web, tunnel = child(100), child(200)
        try:
            with private_files() as files, patch.object(demo, "PORT", server.server_port), \
                    patch.object(demo, "port_available", return_value=True), \
                    patch.object(demo, "spawn_web", return_value=web), \
                    patch.object(demo, "spawn_tunnel", return_value=tunnel) as tunnel_start, \
                    patch.object(demo, "TunnelOutput", return_value=Mock(url=URL, failed=False)), \
                    patch.object(demo, "emit", side_effect=record):
                started = time.monotonic()
                code = demo.launch({}, files, demo.Limits(startup_timeout_seconds=4, poll_seconds=0.05), stop)
                elapsed = time.monotonic() - started
                self.assertEqual(demo.EXIT_OK, code)
                self.assertEqual(["/readyz", "/readyz"], requests)
                self.assertGreaterEqual(elapsed, 1.2)
                self.assertLess(elapsed, 4)
                tunnel_start.assert_called_once()
                self.assertEqual(1, sum(event["event"] == "public_demo_ready" for event in events))
                self.assertEqual("signal", demo._read_state(files)["reason"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_owned_child_crashes_fail_without_restart_or_foreign_process_actions(self):
        for which in ("web", "tunnel"):
            with self.subTest(which=which), private_files() as files:
                web, tunnel = child(100, exited=which == "web"), child(200, exited=which == "tunnel")
                code, events, order, web_spawn, tunnel_spawn, *_ = self.run_launch(files, Clock(), web=web, tunnel=tunnel)
                self.assertEqual(demo.EXIT_CHILD, code)
                self.assertEqual("child_exited", events[-1]["reason"])
                web_spawn.assert_called_once()
                self.assertEqual(["web"] if which == "web" else ["tunnel", "web"], order)
                self.assertLessEqual(tunnel_spawn.call_count, 1)

    def test_unexpected_readiness_error_is_sanitized_and_still_cleans_owned_web(self):
        def failed(_timeout):
            raise RuntimeError("secret-url-password")
        with private_files() as files:
            code, events, order, *_ = self.run_launch(files, Clock(), ready=failed)
            self.assertEqual(demo.EXIT_CHILD, code)
            self.assertEqual(["web"], order)
            self.assertNotIn("secret", json.dumps(events))

    def test_failed_tunnel_shutdown_still_attempts_owned_web_shutdown(self):
        web, tunnel = child(100), child(200)
        clock = Clock()
        with private_files() as files, patch.object(demo.time, "monotonic", clock.monotonic), patch.object(demo, "port_available", return_value=True), patch.object(demo, "spawn_web", return_value=web), patch.object(demo, "spawn_tunnel", return_value=tunnel), patch.object(demo, "local_ready", return_value=True), patch.object(demo, "TunnelOutput", return_value=Mock(url=URL, failed=False)), redirect_stdout(io.StringIO()):
            tunnel.stop.side_effect = OSError("private-detail")
            self.assertEqual(demo.EXIT_SHUTDOWN, demo.launch({}, files, demo.Limits(max_runtime_seconds=0.75), clock))
            web.stop.assert_called_once()

    def test_occupied_port_refuses_without_spawning_or_changing_previous_state(self):
        with private_files() as files, patch.object(demo, "port_available", return_value=False), patch.object(demo, "spawn_web") as web, patch.object(demo, "spawn_tunnel") as tunnel, redirect_stdout(io.StringIO()):
            files.state.write_bytes(b"previous-state")
            self.assertEqual(demo.EXIT_OCCUPIED, demo.launch({}, files, demo.Limits(), Clock()))
            self.assertEqual(b"previous-state", files.state.read_bytes())
            web.assert_not_called()
            tunnel.assert_not_called()

    def test_cli_status_never_loads_private_config_checks_binary_or_starts_children(self):
        with private_files() as files, patch.object(demo, "DemoFiles", return_value=files), patch.object(type(files), "validate"), patch.object(demo, "verify_binary") as verify, patch.object(demo, "demo_environment") as environment, patch.object(demo, "launch") as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(demo.EXIT_OK, demo.main(["--status"]))
            verify.assert_not_called()
            environment.assert_not_called()
            launch.assert_not_called()
            self.assertFalse(files.lock.exists())

    def test_cli_rejects_nonfinite_runtime_and_arbitrary_ports_before_actions(self):
        for arguments in (["--max-runtime-seconds", "nan"], ["--max-runtime-seconds", "inf"],
                          ["--max-runtime-seconds", "0"], ["--port", "8000"], ["--stop", "--status"]):
            with self.subTest(arguments=arguments), patch.object(demo, "DemoFiles") as files, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    demo.main(arguments)
                self.assertEqual(2, error.exception.code)
                files.assert_not_called()

    def test_cli_configuration_failure_never_echoes_error_or_starts_children(self):
        with private_files() as files, patch.object(demo, "DemoFiles", return_value=files), patch.object(type(files), "validate"), patch.object(demo, "verify_binary", side_effect=ValueError("secret-password-url")), patch.object(demo, "launch") as launch, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(demo.EXIT_CONFIG, demo.main([]))
            self.assertEqual({"event": "public_demo_configuration_unavailable"}, json.loads(output.getvalue()))
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
