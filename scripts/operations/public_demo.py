"""Explicit anonymous demo on an owned loopback child and Cloudflare Quick Tunnel.

Traffic passes Cloudflare. No account, router, firewall, autostart, or sleep
settings are changed. Foreground operation lasts until a signal or --stop.
Only sanitized lifecycle state is retained; child output is never printed.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

try:
    from .prototype_supervisor import (
        ROOT, OwnedChild, _WindowsJob, _read_json_response, _socket_ready, _within_json_depth,
        emit, private_environment,
    )
except ImportError:
    from prototype_supervisor import (
        ROOT, OwnedChild, _WindowsJob, _read_json_response, _socket_ready, _within_json_depth,
        emit, private_environment,
    )

HOST = "127.0.0.1"
PORT = 8765
BASE_URL = f"http://{HOST}:{PORT}"
CLOUDFLARED_SHA256 = "f096265ec2fcbe9bb6e2d64268db167ced3fcbb83d894bdb9e2fcdb26f2ea7e2"
PUBLIC_URL = re.compile(r"https://[a-z0-9-]{1,63}\.trycloudflare\.com\Z")
RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
EMPTY_CONFIG = b"{}\n"
MAX_STATE_BYTES = 2048
MAX_STARTUP_LINES = 200
MAX_STARTUP_BYTES = 128 * 1024
MAX_LINE_BYTES = 2048
READINESS_PROBE_SECONDS = 3.0
EXIT_OK, EXIT_CONFIG, EXIT_OCCUPIED, EXIT_LOCKED = 0, 1, 2, 3
EXIT_NOT_READY, EXIT_SHUTDOWN, EXIT_CHILD = 4, 5, 6
PHASES = {"starting", "running", "stopping", "stopped", "failed"}
REASONS = {"requested", "signal", "runtime_limit", "startup_timeout", "child_exited",
           "startup_output_unavailable", "launcher_failed", "shutdown_failed"}


@dataclass(frozen=True)
class Limits:
    startup_timeout_seconds: float = 90.0
    shutdown_timeout_seconds: float = 5.0
    poll_seconds: float = 0.25
    max_runtime_seconds: float | None = None

    def __post_init__(self):
        for value, lower, upper in (
            (self.startup_timeout_seconds, 0.1, 90),
            (self.shutdown_timeout_seconds, 0.1, 30),
            (self.poll_seconds, 0.01, 1),
        ):
            if not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("invalid demo limits")
        if self.max_runtime_seconds is not None and (
            not math.isfinite(self.max_runtime_seconds) or not 0.1 <= self.max_runtime_seconds <= 86400
        ):
            raise ValueError("invalid demo runtime")


@dataclass(frozen=True)
class DemoFiles:
    root: Path = ROOT

    @property
    def local(self):
        return self.root / ".local"

    @property
    def binary(self):
        return self.local / "tools" / "cloudflared-2026.9.3.exe"

    @property
    def config(self):
        return self.local / "public-demo-tunnel.yml"

    @property
    def state(self):
        return self.local / "public-demo-state.json"

    @property
    def state_temp(self):
        return self.local / "public-demo-state.tmp"

    @property
    def stop(self):
        return self.local / "public-demo-stop.json"

    @property
    def lock(self):
        return self.local / "public-demo.lock"

    @property
    def identity_lock(self):
        return self.local / "public-demo-identity.lock"

    def validate(self):
        root = self.root.resolve()
        if self.local.resolve() != root / ".local":
            raise ValueError("invalid demo storage")
        for path in (self.binary, self.config, self.state, self.state_temp, self.stop, self.lock, self.identity_lock):
            if path.resolve() != root / path.relative_to(self.root):
                raise ValueError("invalid demo storage")
            ignored = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", str(path)],
                cwd=root, capture_output=True, timeout=5,
            )
            if ignored.returncode != 0:
                raise ValueError("demo storage must be ignored")


class LaunchLock:
    """An OS lock, rather than a PID file or a check-then-create race."""
    def __init__(self, path: Path, *, create: bool = True):
        self.path, self.create, self.stream = path, create, None

    def __enter__(self):
        self.stream = self.path.open("a+b" if self.create else "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.create and self.path.stat().st_size == 0:
                    self.stream.write(b"\0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise BlockingIOError("demo already running") from None
        return self

    def __exit__(self, *_args):
        if self.stream is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()


def launcher_active(files: DemoFiles) -> bool:
    try:
        with LaunchLock(files.lock, create=False):
            return False
    except FileNotFoundError:
        return False
    except BlockingIOError:
        return True


def _read_state(files: DemoFiles) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("invalid demo state")
            result[key] = value
        return result

    with files.state.open("rb") as stream:
        raw = stream.read(MAX_STATE_BYTES + 1)
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("invalid demo state")
    text = raw.decode("utf-8")
    if not _within_json_depth(text):
        raise ValueError("invalid demo state")
    state = json.loads(text, object_pairs_hook=unique)
    if not isinstance(state, dict) or set(state) - {
        "phase", "run_id", "launcher_pid", "web_pid", "tunnel_pid", "url", "reason"
    }:
        raise ValueError("invalid demo state")
    if (not isinstance(state.get("phase"), str) or state["phase"] not in PHASES
            or not isinstance(state.get("run_id"), str)
            or not RUN_ID.fullmatch(state["run_id"])):
        raise ValueError("invalid demo state")
    for name in ("launcher_pid", "web_pid", "tunnel_pid"):
        if name in state and (type(state[name]) is not int or not 0 < state[name] <= 2 ** 32 - 1):
            raise ValueError("invalid demo state")
    if "url" in state and (not isinstance(state["url"], str) or not PUBLIC_URL.fullmatch(state["url"])):
        raise ValueError("invalid demo state")
    if "reason" in state and (not isinstance(state["reason"], str) or state["reason"] not in REASONS):
        raise ValueError("invalid demo state")
    return state


def _save_state(files: DemoFiles, state: dict):
    # The sole writer holds LaunchLock; fixed temporary file remains private.
    with files.state_temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(state, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    files.state_temp.replace(files.state)


def _begin_run(files: DemoFiles, *, state: dict | None = None) -> dict:
    """Publish this lock holder before any preflight can block or receive Stop."""
    if state is None:
        state = {"phase": "starting", "run_id": uuid.uuid4().hex, "launcher_pid": os.getpid()}
    # Clear the previous request before publishing the new identity. Requests
    # acknowledged against this starting state must remain intact through launch.
    files.stop.unlink(missing_ok=True)
    _save_state(files, state)
    return state


def _stop_requested(files: DemoFiles, run_id: str) -> bool:
    try:
        with files.stop.open("rb") as stream:
            return stream.read(34) == (run_id + "\n").encode("ascii")
    except FileNotFoundError:
        return False


def request_stop(files: DemoFiles) -> int:
    try:
        # Start holds this guard before it acquires the lifetime lock, until
        # fresh state is published. It also prevents a new start replacing the
        # identity between our state read and stop acknowledgement.
        with LaunchLock(files.identity_lock, create=False):
            return _request_stop_initialized(files)
    except FileNotFoundError:
        # Legacy inactive state may have no initialization guard at all. An
        # active lifetime lock without that guard is uninitialized, so retry.
        if not launcher_active(files):
            emit("public_demo_inactive")
            return EXIT_OK
        emit("public_demo_stop_retry")
        return EXIT_LOCKED
    except BlockingIOError:
        # Start can hold the guard before acquiring its lifetime lock. It is
        # still initializing even though launcher_active would report false.
        emit("public_demo_stop_retry")
        return EXIT_LOCKED


def _request_stop_initialized(files: DemoFiles) -> int:
    if not launcher_active(files):
        emit("public_demo_inactive")
        return EXIT_OK
    try:
        state = _read_state(files)
    except FileNotFoundError:
        emit("public_demo_stop_retry")
        return EXIT_LOCKED
    if state["phase"] not in {"starting", "running", "stopping"}:
        # The lock may have just been acquired while the previous terminal state
        # is still visible. Never acknowledge a Stop against that old identity.
        emit("public_demo_stop_retry")
        return EXIT_LOCKED
    try:
        with files.stop.open("xb") as stream:
            stream.write((state["run_id"] + "\n").encode("ascii"))
    except FileExistsError:
        if not _stop_requested(files, state["run_id"]):
            raise ValueError("invalid stop request") from None
    current = _read_state(files)
    if current["run_id"] != state["run_id"]:
        if _stop_requested(files, state["run_id"]):
            files.stop.unlink(missing_ok=True)
        emit("public_demo_stop_retry")
        return EXIT_LOCKED
    emit("public_demo_stop_requested")
    return EXIT_OK


def show_status(files: DemoFiles) -> int:
    active = launcher_active(files)
    try:
        state = _read_state(files)
    except FileNotFoundError:
        emit("public_demo_status", active=active)
    else:
        emit("public_demo_status", active=active, **state)
    return EXIT_OK


def verify_binary(files: DemoFiles):
    hasher = hashlib.sha256()
    with files.binary.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != CLOUDFLARED_SHA256:
        raise ValueError("verified tunnel binary unavailable")


def prepare_tunnel_config(files: DemoFiles):
    try:
        with files.config.open("xb") as stream:
            stream.write(EMPTY_CONFIG)
    except FileExistsError:
        with files.config.open("rb") as stream:
            if stream.read(len(EMPTY_CONFIG) + 1) != EMPTY_CONFIG:
                raise ValueError("invalid demo tunnel configuration") from None


def demo_environment(path: Path) -> dict[str, str]:
    environment = dict(private_environment(path))
    interval = float(environment.get("ACADEMIC_LLM_MIN_INTERVAL_SECONDS", "5"))
    if not math.isfinite(interval) or not 2 <= interval <= 600:
        raise ValueError("invalid demo LLM interval")
    environment["ACADEMIC_LLM_MIN_INTERVAL_SECONDS"] = str(max(60.0, interval))
    environment["ACADEMIC_PUBLIC_DEMO"] = "1"
    return environment


def tunnel_environment() -> dict[str, str]:
    # No application credentials, named tunnel tokens, or inherited CF options.
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith(("TUNNEL_", "CF_", "ACADEMIC_", "NEO4J_"))}


def port_available() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((HOST, PORT))
        except OSError:
            return False
    return True


def local_ready(timeout: float) -> bool:
    """Total-deadline local GET, with no redirects, proxies, or inference."""
    deadline = time.monotonic() + timeout
    try:
        if not math.isfinite(timeout) or timeout <= 0:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.setblocking(False)
            error = connection.connect_ex((HOST, PORT))
            if error not in {0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035, 10036, 10037}:
                return False
            _socket_ready(connection, deadline, writing=True)
            if connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                return False
            request = memoryview((f"GET /readyz HTTP/1.1\r\nHost: {HOST}:{PORT}\r\n"
                                  "Accept: application/json\r\nConnection: close\r\n\r\n").encode("ascii"))
            while request:
                _socket_ready(connection, deadline, writing=True)
                try:
                    sent = connection.send(request)
                except BlockingIOError:
                    continue
                if sent <= 0:
                    return False
                request = request[sent:]
            status, body = _read_json_response(connection, deadline)
            return (status == 200 and isinstance(body, dict)
                    and set(body) == {"status", "public_demo"}
                    and body["status"] == "ready" and body["public_demo"] is True)
    except (OSError, ValueError):
        return False


class ControlledChild(OwnedChild):
    """Ask the gated child to close gracefully before bounded owned fallback."""
    def stop(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        try:
            if self.process.poll() is None and self.process.stdin is not None:
                self.process.stdin.write(b"0")
                self.process.stdin.flush()
                self.process.stdin.close()
                self.process.wait(timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            pass
        # Even a normally exited root must close its job to catch descendants.
        return super().stop(max(0.1, deadline - time.monotonic()))


def _spawn(command: list[str], environment: dict[str, str], timeout: float, *, tunnel=False) -> OwnedChild:
    job = process = None
    try:
        if os.name == "nt":
            job = _WindowsJob()
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE if tunnel else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if tunnel else subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        if job is not None:
            job.assign(process)
        # Both trusted wrappers wait before importing the app or spawning the
        # binary; the job owns their future descendants before this gate opens.
        process.stdin.write(b"1")
        process.stdin.flush()
        return ControlledChild(process, job)
    except (OSError, subprocess.SubprocessError):
        if process is not None:
            OwnedChild(process, job).stop(timeout)
            if process.stdout is not None:
                process.stdout.close()
        elif job is not None:
            job.close()
        raise OSError("owned demo child unavailable") from None


def spawn_web(environment: dict[str, str], timeout: float) -> OwnedChild:
    executable = sys._base_executable if os.name == "nt" else sys.executable
    # The bundled base runtime has global optional native packages. Disable
    # automatic site loading; private_environment already supplies our pinned
    # project dependencies explicitly, including Neo4j without bundled numpy.
    options = ["-S"] if os.name == "nt" else []
    return _spawn([executable, *options, str(Path(__file__).resolve()), "--web-child"], environment, timeout)


def spawn_tunnel(files: DemoFiles, timeout: float) -> OwnedChild:
    # Recheck at the execution boundary; launch never downloads anything.
    verify_binary(files)
    prepare_tunnel_config(files)
    executable = sys._base_executable if os.name == "nt" else sys.executable
    return _spawn([executable, str(Path(__file__).resolve()), "--tunnel-child"],
                  tunnel_environment(), timeout, tunnel=True)


def tunnel_command(files: DemoFiles) -> list[str]:
    return [str(files.binary), "tunnel", "--config", str(files.config),
            "--no-autoupdate", "--url", BASE_URL, "--metrics", "127.0.0.1:0",
            "--protocol", "http2", "--retries", "2"]


def _interrupt_owned_console(process) -> bool:
    """Executed only by the hidden wrapper, targeting its own child's console."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    handler = handler_type(lambda _event: True)
    kernel.AttachConsole.argtypes = [wintypes.DWORD]
    kernel.AttachConsole.restype = wintypes.BOOL
    kernel.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
    kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
    kernel.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
    kernel.FreeConsole.argtypes = []
    kernel.FreeConsole.restype = wintypes.BOOL
    # The wrapper starts with CREATE_NO_WINDOW, so it has no user console to
    # detach. Attach only to the exact Popen child it just created.
    if not kernel.AttachConsole(process.pid):
        return False
    try:
        if not kernel.SetConsoleCtrlHandler(handler, True):
            return False
        try:
            # This new hidden console contains only our cloudflared child and
            # this wrapper. The handler prevents the wrapper from exiting early.
            if not kernel.GenerateConsoleCtrlEvent(1, 0):  # CTRL_BREAK_EVENT
                return False
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                return False
            return True
        finally:
            kernel.SetConsoleCtrlHandler(handler, False)
    finally:
        kernel.FreeConsole()


def _tunnel_child() -> int:
    if sys.stdin.buffer.read(1) != b"1":
        return EXIT_CONFIG
    files = DemoFiles()
    files.validate()
    verify_binary(files)
    prepare_tunnel_config(files)
    options = {}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0  # SW_HIDE; console exists for graceful CTRL_BREAK.
        options = {"creationflags": subprocess.CREATE_NEW_CONSOLE,
                   "startupinfo": startup}
    process = subprocess.Popen(tunnel_command(files), cwd=ROOT, env=tunnel_environment(),
                               stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT, **options)
    requested = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.buffer.read(1), requested.set()), daemon=True).start()
    try:
        while process.poll() is None and not requested.wait(0.1):
            pass
        if process.poll() is None:
            if os.name == "nt":
                interrupted = _interrupt_owned_console(process)
                if not interrupted:
                    process.terminate()
            else:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        return process.returncode or EXIT_OK
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)


class TunnelOutput:
    """Retain at most one exact URL; all raw logs are discarded."""
    def __init__(self, stream):
        self.stream, self.url = stream, None
        self.failed = False
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True, name="public-demo-startup")
        self.thread.start()

    def _read(self):
        total = lines = 0
        try:
            while not self.finished.is_set():
                raw = self.stream.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    return
                total += len(raw)
                lines += 1
                if len(raw) > MAX_LINE_BYTES or total > MAX_STARTUP_BYTES or lines > MAX_STARTUP_LINES:
                    self.failed = True
                    break
                for token in raw.decode("utf-8", errors="replace").split():
                    if PUBLIC_URL.fullmatch(token):
                        if self.url is not None and self.url != token:
                            self.failed = True
                            break
                        self.url = token
            # Continue draining bounded chunks; request/error logs never persist.
            while self.stream.read(4096):
                pass
        except (OSError, ValueError):
            self.failed = True

    def close(self):
        self.finished.set()
        self.thread.join(timeout=1)
        if self.thread.is_alive():
            # BufferedReader.close can wait indefinitely for another thread's
            # blocking read lock. Report incomplete cleanup instead of waiting.
            return False
        self.stream.close()
        return True


def launch(environment: dict[str, str], files: DemoFiles, limits: Limits, stop: threading.Event,
           *, state: dict | None = None) -> int:
    web = tunnel = output = None
    result, reason = EXIT_OK, "requested"
    now = time.monotonic()
    startup_deadline = now + limits.startup_timeout_seconds
    runtime_deadline = math.inf if limits.max_runtime_seconds is None else now + limits.max_runtime_seconds
    initialized = state is not None
    if not initialized and not port_available():
        emit("public_demo_port_occupied")
        return EXIT_OCCUPIED
    if not initialized:
        state = {"phase": "starting", "run_id": uuid.uuid4().hex, "launcher_pid": os.getpid()}
    try:
        if not initialized:
            _begin_run(files, state=state)
        emit("public_demo_starting", traffic_via="cloudflare")
        if stop.is_set() or _stop_requested(files, state["run_id"]):
            reason = "signal" if stop.is_set() else "requested"
            return EXIT_OK
        if initialized and not port_available():
            result, reason = EXIT_OCCUPIED, "launcher_failed"
            emit("public_demo_port_occupied")
            return result
        web = spawn_web(environment, limits.shutdown_timeout_seconds)
        state["web_pid"] = web.process.pid
        _save_state(files, state)
        while True:
            now = time.monotonic()
            if stop.is_set():
                reason = "signal"
                break
            if _stop_requested(files, state["run_id"]):
                reason = "requested"
                break
            if now >= runtime_deadline:
                reason = "runtime_limit"
                break
            if web.process.poll() is not None or (tunnel is not None and tunnel.process.poll() is not None):
                result, reason = EXIT_CHILD, "child_exited"
                break
            if state["phase"] == "starting":
                if now >= startup_deadline:
                    result, reason = EXIT_NOT_READY, "startup_timeout"
                    break
                if output is not None and output.failed:
                    result, reason = EXIT_NOT_READY, "startup_output_unavailable"
                    break
                remaining = min(startup_deadline, runtime_deadline) - now
                if ((tunnel is None or (output is not None and output.url is not None))
                        and local_ready(min(READINESS_PROBE_SECONDS, remaining))
                        and time.monotonic() < min(startup_deadline, runtime_deadline)
                        and not stop.is_set() and not _stop_requested(files, state["run_id"])):
                    if tunnel is None:
                        tunnel = spawn_tunnel(files, limits.shutdown_timeout_seconds)
                        output = TunnelOutput(tunnel.process.stdout)
                        state["tunnel_pid"] = tunnel.process.pid
                        _save_state(files, state)
                    elif (time.monotonic() < min(startup_deadline, runtime_deadline)
                          and not output.failed and tunnel.process.poll() is None):
                        state.update(phase="running", url=output.url)
                        output.finished.set()
                        _save_state(files, state)
                        emit("public_demo_ready", url=output.url, local_ready=True, remote_verified=False)
            wait_deadline = runtime_deadline
            if state["phase"] == "starting":
                wait_deadline = min(wait_deadline, startup_deadline)
            stop.wait(min(limits.poll_seconds, max(0.0, wait_deadline - time.monotonic())))
    except KeyboardInterrupt:
        stop.set()
        reason = "signal"
    except Exception:
        result, reason = EXIT_CHILD, "launcher_failed"
        emit("public_demo_failed")
    finally:
        state.update(phase="stopping", reason=reason)
        try:
            _save_state(files, state)
        except OSError:
            result = EXIT_SHUTDOWN
        # The public tunnel closes before its local application. No foreign PID
        # or port owner is ever consulted or terminated, even for stale state.
        for child in (tunnel, web):
            if child is not None:
                try:
                    if not child.stop(limits.shutdown_timeout_seconds):
                        result = EXIT_SHUTDOWN
                except Exception:
                    result = EXIT_SHUTDOWN
        if output is not None:
            try:
                if not output.close():
                    result = EXIT_SHUTDOWN
            except OSError:
                result = EXIT_SHUTDOWN
        if result == EXIT_SHUTDOWN:
            reason = "shutdown_failed"
        state.update(phase="stopped" if result == EXIT_OK else "failed", reason=reason)
        try:
            _save_state(files, state)
            if _stop_requested(files, state["run_id"]):
                files.stop.unlink(missing_ok=True)
        except OSError:
            result = EXIT_SHUTDOWN
        emit("public_demo_stopped", reason=reason, clean=result != EXIT_SHUTDOWN)
    return result


def _web_child() -> int:
    if sys.stdin.buffer.read(1) != b"1" or os.environ.get("ACADEMIC_PUBLIC_DEMO") != "1":
        return EXIT_CONFIG
    import uvicorn
    server = uvicorn.Server(uvicorn.Config("academic_assistant.public_demo:create_demo_app", factory=True,
                                         host=HOST, port=PORT, workers=1,
                                         access_log=False, log_level="critical"))
    def request_shutdown():
        sys.stdin.buffer.read(1)
        server.should_exit = True
    threading.Thread(target=request_shutdown, daemon=True).start()
    server.run()
    return EXIT_OK


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--stop", action="store_true", help="request the owned launcher to stop")
    action.add_argument("--status", action="store_true", help="read sanitized own lifecycle state")
    parser.add_argument("--max-runtime-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    try:
        limits = Limits(max_runtime_seconds=args.max_runtime_seconds)
    except ValueError:
        parser.error("demo runtime bound is invalid")
    files = DemoFiles()
    try:
        files.validate()
        if args.stop:
            return request_stop(files)
        if args.status:
            return show_status(files)
        files.local.mkdir(exist_ok=True)
        with ExitStack() as lifetime:
            with LaunchLock(files.identity_lock):
                lifetime.enter_context(LaunchLock(files.lock))
                state = _begin_run(files)
            stop = threading.Event()
            previous = {}
            try:
                for name in (signal.SIGINT, signal.SIGTERM):
                    previous[name] = signal.signal(name, lambda _signum, _frame: stop.set())
                if not stop.is_set() and not _stop_requested(files, state["run_id"]):
                    verify_binary(files)
                if not stop.is_set() and not _stop_requested(files, state["run_id"]):
                    prepare_tunnel_config(files)
                environment = {}
                if not stop.is_set() and not _stop_requested(files, state["run_id"]):
                    environment = demo_environment(ROOT / ".local" / "prototype.json")
                return launch(environment, files, limits, stop, state=state)
            except (OSError, ValueError, ImportError, subprocess.SubprocessError):
                state.update(phase="failed", reason="launcher_failed")
                _save_state(files, state)
                raise
            finally:
                for name, handler in previous.items():
                    signal.signal(name, handler)
    except BlockingIOError:
        emit("public_demo_already_running")
        return EXIT_LOCKED
    except (OSError, ValueError, ImportError, subprocess.SubprocessError):
        emit("public_demo_configuration_unavailable")
        return EXIT_CONFIG


if __name__ == "__main__":
    if sys.argv[1:] in (["--web-child"], ["--tunnel-child"]):
        try:
            raise SystemExit(_web_child() if sys.argv[1] == "--web-child" else _tunnel_child())
        except Exception:
            raise SystemExit(EXIT_CONFIG) from None
    raise SystemExit(main())
