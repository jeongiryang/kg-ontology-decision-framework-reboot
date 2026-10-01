from __future__ import annotations

import io
import socket
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "operations"))
import ssh_loopback_bridge as bridge

REAL_POPEN = subprocess.Popen
REAL_THREAD = threading.Thread
RESPONSE = b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + b'{"synthetic":"' + b"a" * 18000 + b'"}'


class LoopbackBridgeTests(unittest.TestCase):
    def command(self, code):
        return [sys.executable, "-I", "-u", "-c", code]

    def run_owned_relay(self, command, *, request=b"GET /synthetic HTTP/1.0\r\n\r\n", half_close=False, spawn_error=None):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0)); listener.listen(1)
        caller = socket.create_connection(listener.getsockname(), timeout=3)
        accepted, _ = listener.accept(); listener.close()
        slots = threading.BoundedSemaphore(1); slots.acquire()
        children, uploads, errors = [], [], []
        def spawn(*args, **kwargs):
            if spawn_error: raise spawn_error
            child = REAL_POPEN(*args, **kwargs); children.append(child); return child
        def thread(*args, **kwargs):
            upload = REAL_THREAD(*args, **kwargs); uploads.append(upload); return upload
        def invoke():
            try: bridge.relay(accepted, command, slots)
            except Exception as exc: errors.append(exc)
        worker = REAL_THREAD(target=invoke, name="owned-relay-fixture")
        data, reset = bytearray(), None
        with patch.object(bridge.subprocess, "Popen", side_effect=spawn), patch.object(bridge.threading, "Thread", side_effect=thread):
            worker.start()
            try:
                if request: caller.sendall(request)
                if half_close: caller.shutdown(socket.SHUT_WR)
                try:
                    while part := caller.recv(65536): data.extend(part)
                except (ConnectionResetError, ConnectionAbortedError) as exc:
                    reset = exc
            finally:
                try: caller.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                caller.close(); worker.join(5)
                for child in children:
                    if child.poll() is None: child.kill(); child.wait(timeout=3)
                for upload in uploads: upload.join(1)
        self.assertFalse(worker.is_alive(), "relay fixture must finish")
        self.assertEqual([], errors)
        self.assertTrue(slots.acquire(blocking=False), "relay must release its owned slot")
        slots.release()
        self.assertTrue(all(not upload.is_alive() for upload in uploads), "upload threads must not leak")
        self.assertTrue(all(child.poll() is not None for child in children), "owned child must be reaped")
        return bytes(data), reset, children

    def test_connection_delimited_exact_response_reaches_graceful_eof(self):
        code = "import sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write(" + repr(RESPONSE) + "); sys.stdout.buffer.flush()"
        data, reset, _ = self.run_owned_relay(self.command(code))
        self.assertIsNone(reset, "complete unknown-length HTTP response must finish with EOF, not reset")
        self.assertEqual(RESPONSE, data)

    def test_input_half_close_propagates_eof_without_losing_response(self):
        code = "import sys; request=sys.stdin.buffer.read(); sys.stdout.buffer.write(request); sys.stdout.buffer.flush()"
        data, reset, _ = self.run_owned_relay(self.command(code), request=b"synthetic full upload", half_close=True)
        self.assertIsNone(reset); self.assertEqual(b"synthetic full upload", data)

    def test_child_exit_wakes_blocked_pipe_upload_and_reaps_owned_child(self):
        code = "import os,time; time.sleep(.1); os.write(1," + repr(RESPONSE[:100]) + ")"
        data, reset, children = self.run_owned_relay(self.command(code), request=b"u" * 65536)
        self.assertIsNone(reset); self.assertEqual(RESPONSE[:100], data)
        self.assertEqual(1, len(children)); self.assertIsNotNone(children[0].returncode)

    def test_child_failure_releases_slot_and_finishes_upload(self):
        data, reset, children = self.run_owned_relay(self.command("raise SystemExit(7)"))
        self.assertIsNone(reset); self.assertEqual(b"", data); self.assertEqual(7, children[0].returncode)

    def test_spawn_failure_releases_slot_without_other_process_action(self):
        data, reset, children = self.run_owned_relay(["not-executed"], spawn_error=OSError("synthetic spawn failure"))
        self.assertIsNone(reset); self.assertEqual(b"", data); self.assertEqual([], children)

    def test_windows_and_posix_creation_flags_and_owned_pipe_closure(self):
        for platform, expected in (("nt", 0x08000000), ("posix", 0)):
            with self.subTest(platform=platform):
                child = Mock()
                child.stdin = io.BytesIO(); child.stdout = Mock(); child.stdout.fileno.return_value = 123
                child.poll.return_value = 0
                client = Mock(); client.recv.return_value = b""
                slots = threading.BoundedSemaphore(1); slots.acquire()
                with patch.object(bridge.os, "name", platform), patch.object(bridge.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                     patch.object(bridge.subprocess, "Popen", return_value=child) as spawn, patch.object(bridge.os, "read", return_value=b""):
                    bridge.relay(client, ["synthetic-command"], slots)
                self.assertEqual(expected, spawn.call_args.kwargs["creationflags"])
                child.terminate.assert_not_called(); child.kill.assert_not_called()
                self.assertTrue(child.stdin.closed)
                child.stdout.close.assert_called_once()
                client.close.assert_called_once()
                self.assertTrue(slots.acquire(blocking=False)); slots.release()

    def test_download_eof_wakes_recv_and_kills_only_owned_stuck_child(self):
        release = threading.Event()
        client = Mock(); client.recv.side_effect = lambda _: release.wait(3) and b""
        client.shutdown.side_effect = lambda _: release.set()
        child = Mock(); child.stdin = io.BytesIO(); child.stdout.fileno.return_value = 3
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("synthetic-owned", 3), 0]
        slots = threading.BoundedSemaphore(1); slots.acquire()
        with patch.object(bridge.subprocess, "Popen", return_value=child) as spawn, patch.object(bridge.os, "read", return_value=b""):
            bridge.relay(client, ["synthetic-owned"], slots)
        self.assertEqual(1, spawn.call_count)
        child.terminate.assert_called_once(); child.kill.assert_called_once()
        self.assertEqual(2, child.wait.call_count)
        self.assertEqual([((socket.SHUT_WR,), {}), ((socket.SHUT_RD,), {})], client.shutdown.call_args_list)
        self.assertTrue(child.stdin.closed); child.stdout.close.assert_called_once(); client.close.assert_called_once()
        self.assertTrue(slots.acquire(blocking=False)); slots.release()

    def test_downstream_send_failure_still_closes_pipes_socket_and_releases_slot(self):
        child = Mock(); child.stdin = io.BytesIO(); child.stdout.fileno.return_value = 3
        child.poll.return_value = 0; child.wait.return_value = 0
        client = Mock(); client.recv.return_value = b""; client.sendall.side_effect = ConnectionResetError("synthetic reset")
        slots = threading.BoundedSemaphore(1); slots.acquire()
        with patch.object(bridge.subprocess, "Popen", return_value=child), patch.object(bridge.os, "read", return_value=b"response"):
            bridge.relay(client, ["synthetic-owned"], slots)
        child.terminate.assert_not_called(); child.kill.assert_not_called()
        client.close.assert_called_once(); child.stdout.close.assert_called_once(); self.assertTrue(child.stdin.closed)
        self.assertTrue(slots.acquire(blocking=False)); slots.release()


if __name__ == "__main__":
    unittest.main()
