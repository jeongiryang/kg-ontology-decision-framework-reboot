"""Bounded supervision of one private web child at 127.0.0.1:8000.

Only sanitized state transitions go to stdout; child output is discarded.
Check mode performs two bounded GETs, never inference or process management.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

try:
    from .start_prototype import KEYS, ROOT, load_private_settings
except ImportError:
    from start_prototype import KEYS, ROOT, load_private_settings

HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"
MAX_RESPONSE_BYTES = 8192
MAX_HEADER_BYTES = 8192
MAX_JSON_DEPTH = 64
EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_OCCUPIED = 2
EXIT_RESTARTS = 3
EXIT_NOT_READY = 4
EXIT_SHUTDOWN = 5


@dataclass(frozen=True)
class Limits:
    max_restarts: int = 3
    backoff_seconds: float = 1.0
    max_backoff_seconds: float = 10.0
    poll_seconds: float = 5.0
    startup_timeout_seconds: float = 30.0
    shutdown_timeout_seconds: float = 5.0
    max_runtime_seconds: float = 3600.0

    def __post_init__(self):
        if type(self.max_restarts) is not int or not 0 <= self.max_restarts <= 3:
            raise ValueError("invalid restart bound")
        bounds = (
            (self.backoff_seconds, 0.1, 10),
            (self.max_backoff_seconds, 0.1, 10),
            (self.poll_seconds, 0.1, 60),
            (self.startup_timeout_seconds, 1, 120),
            (self.shutdown_timeout_seconds, 0.1, 30),
            (self.max_runtime_seconds, 0.1, 3600),
        )
        if any(not math.isfinite(value) or not low <= value <= high for value, low, high in bounds):
            raise ValueError("invalid supervision limits")
        if self.backoff_seconds > self.max_backoff_seconds:
            raise ValueError("invalid backoff limits")


def emit(event: str, **fields):
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def private_environment(path: Path) -> dict[str, str]:
    """Validate the ignored configuration without changing this process's env."""
    config = load_private_settings(path)
    sys.path.insert(0, str(ROOT / "src"))
    from academic_assistant.llm import LLMSettings
    from academic_assistant.neo4j_evidence import Neo4jSettings
    if Neo4jSettings.from_env(config) is None:
        raise ValueError("explicit Neo4j configuration required")
    LLMSettings.from_env(config)
    environment = {
        key: value for key, value in os.environ.items()
        if key not in KEYS and not key.upper().startswith(("ACADEMIC_", "NEO4J_"))
    }
    environment.update(config)
    paths = [str(ROOT / "src")]
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        # A Windows venv python.exe is a launcher: its runtime can exist before
        # job assignment. Start the base runtime directly, retaining our deps.
        environment["VIRTUAL_ENV"] = sys.prefix
        paths.append(str(Path(sys.prefix) / "Lib" / "site-packages"))
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid diagnostic response")
        result[key] = value
    return result


def _within_json_depth(text: str) -> bool:
    """Count simultaneous containers, ignoring quoted/escaped characters.

    Syntax and matching container types remain the decoder's responsibility.
    """
    depth = 0
    quoted = False
    escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return False
        elif character in "]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quoted


def _socket_ready(connection: socket.socket, deadline: float, *, writing=False):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("diagnostic deadline")
    readable, writable, failed = select.select(
        [] if writing else [connection], [connection] if writing else [], [connection], remaining,
    )
    if failed:
        raise OSError("diagnostic connection unavailable")
    if not (readable or writable) or time.monotonic() >= deadline:
        raise TimeoutError("diagnostic deadline")


def _receive(connection: socket.socket, deadline: float) -> bytes:
    while True:
        _socket_ready(connection, deadline)
        try:
            return connection.recv(4096)
        except BlockingIOError:
            continue


def _read_json_response(connection: socket.socket, deadline: float):
    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        chunk = _receive(connection, deadline)
        if not chunk:
            raise ValueError("incomplete diagnostic headers")
        raw.extend(chunk)
        if len(raw) > MAX_HEADER_BYTES and b"\r\n\r\n" not in raw:
            raise ValueError("diagnostic headers exceed bound")
    boundary = raw.index(b"\r\n\r\n")
    if boundary > MAX_HEADER_BYTES:
        raise ValueError("diagnostic headers exceed bound")
    lines = bytes(raw[:boundary]).split(b"\r\n")
    status_parts = lines[0].split(b" ", 2)
    if (len(status_parts) < 2 or status_parts[0] not in {b"HTTP/1.0", b"HTTP/1.1"}
            or len(status_parts[1]) != 3 or not status_parts[1].isdigit()):
        raise ValueError("invalid diagnostic status")
    status = int(status_parts[1])
    if status != 200:
        return status, None  # Never retain or wait for an error/redirect body.
    headers = {}
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        name = name.lower()
        if not separator or not name or name in headers or name.strip() != name:
            raise ValueError("invalid diagnostic headers")
        headers[name] = value.strip()
    # These fixed JSON endpoints use Content-Length or connection close.
    # Reject unsupported/ambiguous framing rather than accepting partial JSON.
    if b"transfer-encoding" in headers:
        raise ValueError("invalid diagnostic framing")
    size = headers.get(b"content-length")
    if size is not None:
        if not size.isdigit() or len(size) > 5:
            raise ValueError("invalid diagnostic size")
        size = int(size)
        if size > MAX_RESPONSE_BYTES:
            return status, None
    body = raw[boundary + 4:]
    while size is None or len(body) < size:
        if len(body) > MAX_RESPONSE_BYTES:
            return status, None
        chunk = _receive(connection, deadline)
        if not chunk:
            if size is not None and len(body) != size:
                raise ValueError("incomplete diagnostic body")
            break
        body.extend(chunk)
    if len(body) > MAX_RESPONSE_BYTES or (size is not None and len(body) != size):
        return status, None
    try:
        # Fixed diagnostic endpoints emit UTF-8. Passing text to json.loads
        # also prevents its raw-byte UTF-16/32 autodetection from bypassing
        # the same string/escape-aware scan used for every accepted body.
        text = body.decode("utf-8")
        if not _within_json_depth(text):
            return None, None
        data = json.loads(text, object_pairs_hook=_unique_json)
    except (RecursionError, MemoryError):
        # Retain capacity failure protection even below the explicit depth
        # bound, without changing global decoder limits.
        return None, None
    return status, data if isinstance(data, dict) else None


def _get_json(path: str, timeout: float):
    """One owned socket and a total deadline; no proxy, redirect, thread or timer."""
    deadline = time.monotonic() + timeout
    try:
        endpoint = urlsplit(BASE_URL)
        if (not math.isfinite(timeout) or timeout <= 0
                or path not in {"/readyz", "/v1/academic/runtime"}
                or endpoint.scheme != "http" or endpoint.hostname != HOST
                or endpoint.port is None or endpoint.username or endpoint.password
                or endpoint.path or endpoint.query or endpoint.fragment):
            raise ValueError("invalid diagnostic endpoint")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.setblocking(False)
            error = connection.connect_ex((HOST, endpoint.port))
            pending = {0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035, 10036, 10037}
            if error not in pending:
                raise OSError("diagnostic connection unavailable")
            _socket_ready(connection, deadline, writing=True)
            if connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                raise OSError("diagnostic connection unavailable")
            request = memoryview((f"GET {path} HTTP/1.1\r\nHost: {HOST}:{endpoint.port}\r\n"
                                  "Accept: application/json\r\nCache-Control: no-store\r\n"
                                  "Connection: close\r\n\r\n").encode("ascii"))
            while request:
                _socket_ready(connection, deadline, writing=True)
                try:
                    sent = connection.send(request)
                except BlockingIOError:
                    continue
                if sent <= 0:
                    raise OSError("diagnostic connection unavailable")
                request = request[sent:]
            return _read_json_response(connection, deadline)
    except (OSError, ValueError):
        return None, None


def diagnostic(timeout: float = 3.0) -> dict[str, str]:
    deadline = time.monotonic() + 2 * timeout
    ready_code, ready = _get_json("/readyz", timeout)
    remaining = deadline - time.monotonic()
    runtime_code, runtime = _get_json("/v1/academic/runtime", min(timeout, max(0.0, remaining)))
    verified = (runtime_code == 200 and runtime is not None
                and runtime.get("evidence_backend") == "neo4j"
                and runtime.get("graph_verified") is True)
    is_ready = ready_code == 200 and ready == {"status": "ready"} and verified
    llm = "unknown"
    if verified:
        if runtime.get("llm_configured") is False:
            llm = "disabled"
        elif runtime.get("llm_configured") is True:
            if runtime.get("llm_model_available") is True:
                llm = "available"
            elif runtime.get("llm_model_available") is False:
                llm = "unavailable"
    return {
        "web": "available" if ready_code is not None or runtime_code is not None else "unavailable",
        "readiness": "ready" if is_ready else "unavailable",
        "graph": "verified" if verified else "unavailable",
        "optional_llm": llm,
        "answer_mode": "rule_answers" if is_ready else "unavailable",
    }


def port_available() -> bool:
    """Reject an occupied port without inspecting or terminating its owner."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((HOST, PORT))
        except OSError:
            return False
    return True


def web_listening(timeout: float) -> bool:
    """Startup liveness does not wait for graph queries or model inventory."""
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout):
            return True
    except OSError:
        return False


class _WindowsJob:
    """Own only the assigned child tree; closing this job also handles crashes."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits), ("io", IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("owned process isolation unavailable")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("owned process isolation unavailable")

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("owned process isolation unavailable")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


@dataclass
class OwnedChild:
    process: subprocess.Popen
    job: _WindowsJob | None = None

    def stop(self, timeout: float) -> bool:
        """Only the Popen child/its isolated group can receive termination."""
        try:
            if self.process.poll() is None:
                if os.name == "nt":
                    self.process.terminate()
                else:
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        self.process.kill()
                    else:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=timeout)
            return self.process.poll() is not None
        except (OSError, subprocess.SubprocessError):
            return self.process.poll() is not None
        finally:
            if self.job is not None:
                self.job.close()
            if self.process.stdin is not None:
                self.process.stdin.close()


def spawn_web(environment: dict[str, str], shutdown_timeout: float) -> OwnedChild:
    job = None
    process = None
    try:
        if os.name == "nt":
            job = _WindowsJob()
        executable = sys._base_executable if os.name == "nt" else sys.executable
        process = subprocess.Popen(
            [executable, str(Path(__file__).resolve()), "--web-child"],
            cwd=ROOT, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        if job is not None:
            job.assign(process)
        # Trusted child waits before importing the app or creating descendants.
        process.stdin.write(b"1")
        process.stdin.close()
        return OwnedChild(process, job)
    except (OSError, subprocess.SubprocessError):
        if process is not None:
            OwnedChild(process, job).stop(shutdown_timeout)
        elif job is not None:
            job.close()
        raise OSError("owned web child unavailable") from None


def supervise(environment: dict[str, str], limits: Limits, stop: threading.Event) -> int:
    deadline = time.monotonic() + limits.max_runtime_seconds
    restarts = 0
    result = EXIT_OK
    child = None
    previous_state = None
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            if child is None:
                if not port_available():
                    emit("port_occupied")
                    result = EXIT_OCCUPIED
                    break
                try:
                    child = spawn_web(environment, limits.shutdown_timeout_seconds)
                except OSError:
                    emit("child_start_failed")
                started = time.monotonic()
                responded = False
                previous_state = None
                if child is not None:
                    emit("child_started", child_pid=child.process.pid, restart_count=restarts)
            exited = child is None or child.process.poll() is not None
            if not exited:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or stop.is_set():
                    break
                state = diagnostic(timeout=min(3.0, max(0.01, remaining / 2)))
                responded = (responded or state["web"] == "available"
                             or web_listening(min(1.0, max(0.01, deadline - time.monotonic()))))
                if state != previous_state:
                    emit("diagnostic", **state)
                    previous_state = state
                if not responded and time.monotonic() - started >= limits.startup_timeout_seconds:
                    emit("child_startup_timeout")
                    exited = True
            if exited:
                if child is not None:
                    if not child.stop(limits.shutdown_timeout_seconds):
                        result = EXIT_SHUTDOWN
                        break
                    child = None
                    emit("child_exited")
                if restarts >= limits.max_restarts:
                    emit("restart_budget_exhausted", restart_count=restarts)
                    result = EXIT_RESTARTS
                    break
                delay = min(limits.backoff_seconds * 2 ** restarts, limits.max_backoff_seconds)
                restarts += 1
                emit("restart_backoff", restart_count=restarts, seconds=delay)
                stop.wait(min(delay, max(0.0, deadline - time.monotonic())))
                continue
            stop.wait(min(limits.poll_seconds, max(0.0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        stop.set()
    finally:
        if child is not None:
            if child.stop(limits.shutdown_timeout_seconds):
                emit("child_stopped")
            else:
                emit("child_shutdown_failed")
                result = EXIT_SHUTDOWN
    if result == EXIT_OK:
        emit("supervisor_stopped", reason="signal" if stop.is_set() else "runtime_limit")
    return result


def _web_child() -> int:
    if sys.stdin.buffer.read(1) != b"1":
        return EXIT_CONFIG
    from academic_assistant.llm import LLMSettings
    from academic_assistant.neo4j_evidence import Neo4jSettings
    if Neo4jSettings.from_env() is None:
        return EXIT_CONFIG
    LLMSettings.from_env()
    import uvicorn
    uvicorn.run("academic_assistant.api:app", host=HOST, port=PORT, workers=1,
                access_log=False, log_level="critical")
    return EXIT_OK


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / ".local" / "prototype.json")
    parser.add_argument("--check-once", action="store_true", help="read-only diagnostic; no spawn or inference")
    defaults = Limits()
    parser.add_argument("--max-restarts", type=int, default=defaults.max_restarts)
    for name in ("backoff_seconds", "max_backoff_seconds", "poll_seconds", "startup_timeout_seconds",
                 "shutdown_timeout_seconds", "max_runtime_seconds"):
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=getattr(defaults, name))
    args = parser.parse_args(argv)
    try:
        limits = Limits(**{name: getattr(args, name) for name in Limits.__dataclass_fields__})
    except ValueError:
        parser.error("supervision bounds are invalid")
    if args.check_once:
        state = diagnostic()
        emit("diagnostic", **state)
        return EXIT_OK if state["readiness"] == "ready" else EXIT_NOT_READY
    try:
        environment = private_environment(args.config)
    except (OSError, ValueError, ImportError):
        emit("configuration_unavailable")
        return EXIT_CONFIG
    stop = threading.Event()
    previous = {}
    try:
        for name in (signal.SIGINT, signal.SIGTERM):
            previous[name] = signal.signal(name, lambda _signum, _frame: stop.set())
        return supervise(environment, limits, stop)
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)


if __name__ == "__main__":
    if sys.argv[1:] == ["--web-child"]:
        try:
            raise SystemExit(_web_child())
        except (OSError, ValueError, ImportError):
            raise SystemExit(EXIT_CONFIG) from None
    raise SystemExit(main())
