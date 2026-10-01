"""Run every PDFium/OCR stage in a bounded local child process."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event, Thread
import time

from pydantic import ValidationError

from .transcript_models import TranscriptExtraction

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 2_000_000
EXTRACTION_TIMEOUT_SECONDS = 120
CLEANUP_TIMEOUT_SECONDS = 3
PUBLIC_MEMORY_LIMIT_BYTES = 768 * 1024 * 1024
ERROR_MESSAGE = "성적표를 읽을 수 없습니다. 파일·페이지를 확인하거나 직접 입력해 주세요."


class _WindowsJob:
    """An unnamed job contains only this child and descendants it creates."""
    def __init__(self, memory_limit_bytes: int | None = None):
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
            raise OSError("Process isolation unavailable")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit_bytes is not None:
            # Aggregate committed memory of this owned job, including descendants.
            limits.basic.flags |= 0x200  # JOB_OBJECT_LIMIT_JOB_MEMORY
            limits.job_memory = memory_limit_bytes
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("Process isolation unavailable")

    def assign(self, process):
        # The trusted child blocks reading stdin before it can launch OCR, so
        # assignment completes before any owned descendant can be created.
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("Process isolation unavailable")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _terminate_owned(process, job):
    """Kill this job/group only; direct-child waiting always has a deadline."""
    if job is not None:
        job.close()
    elif os.name != "nt" and type(process.pid) is int and process.pid > 0:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # If Windows assignment failed, stdin was never sent and the trusted child
    # cannot have launched OCR. This fallback terminates that direct child.
    if process.poll() is None:
        process.kill()
    process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)


def _apply_public_memory_limit() -> None:
    """Windows limits are installed by the parent before sending any input."""
    if os.name != "nt" and os.environ.get("ACADEMIC_PUBLIC_DEMO") == "1":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (PUBLIC_MEMORY_LIMIT_BYTES, PUBLIC_MEMORY_LIMIT_BYTES))


def _preview_child_environment() -> dict[str, str]:
    """Keep only OS bootstrap values; never inherit credentials or LLM settings."""
    allowed = {"systemroot", "windir", "temp", "tmp"}
    environment = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    source_root = Path(__file__).resolve().parent.parent
    virtual_environment = source_root.parent / ".venv"
    packages = (virtual_environment / "Lib" / "site-packages" if os.name == "nt" else
                virtual_environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
    paths = [str(source_root)]
    # The public server uses the base runtime on Windows. Retain its pinned
    # project dependencies without accepting arbitrary inherited Python paths.
    if packages.is_dir():
        paths.append(str(packages))
    environment.update({"PYTHONPATH": os.pathsep.join(paths),
                        "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1",
                        "ACADEMIC_PUBLIC_DEMO": "1"})
    return environment


def _transcript_child_environment() -> dict[str, str]:
    """Public OCR inherits bootstrap values only, with a trusted OS search path."""
    environment = _preview_child_environment()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        kernel.GetSystemDirectoryW.restype = wintypes.UINT
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel.GetSystemDirectoryW(buffer, len(buffer))
        if not 0 < size < len(buffer):
            raise OSError("OCR bootstrap unavailable")
        system_directory = Path(buffer.value)
        if not system_directory.is_absolute():
            raise OSError("OCR bootstrap unavailable")
        environment.update({
            "SYSTEMROOT": str(system_directory.parent), "WINDIR": str(system_directory.parent),
            "PATH": os.pathsep.join((str(system_directory / "WindowsPowerShell" / "v1.0"),
                                     str(system_directory))),
            "PATHEXT": ".EXE", "NoDefaultCurrentDirectoryInExePath": "1",
        })
    return environment


def _run_bounded_child(arguments: list[str], data: bytes, *, environment: dict[str, str],
                       timeout_seconds: float, output_limit: int) -> bytes:
    """Bound retained stdout during reading, and kill only the owned tree on exit."""
    deadline = time.monotonic() + timeout_seconds
    process = None
    job = None
    threads = []
    try:
        if os.name == "nt":
            job = _WindowsJob(memory_limit_bytes=PUBLIC_MEMORY_LIMIT_BYTES)
        process = subprocess.Popen(
            arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        if job is not None:
            job.assign(process)
        received, sent = Event(), Event()
        output = []
        errors = []

        def read():
            try:
                output.append(process.stdout.read(output_limit + 1))
            except (OSError, ValueError) as error:
                errors.append(error)
            finally:
                received.set()

        def write():
            try:
                process.stdin.write(data)
                process.stdin.close()
            except (OSError, ValueError) as error:
                errors.append(error)
            finally:
                sent.set()

        threads = [Thread(target=read, daemon=True), Thread(target=write, daemon=True)]
        for thread in threads:
            thread.start()
        for done in (received, sent):
            if not done.wait(max(0, deadline - time.monotonic())):
                raise subprocess.TimeoutExpired(arguments, timeout_seconds)
            if output and len(output[0]) > output_limit:
                raise ValueError("child output limit")
        process.wait(timeout=max(0, deadline - time.monotonic()))
        if errors or process.returncode != 0 or not output:
            raise ValueError("child unavailable")
        return output[0]
    finally:
        try:
            if process is not None:
                _terminate_owned(process, job)
        finally:
            if job is not None:
                job.close()
            for thread in threads:
                thread.join(timeout=CLEANUP_TIMEOUT_SECONDS)
            if process is not None:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass


def extract_isolated(data: bytes, *, page_number: int | None = None) -> TranscriptExtraction:
    """Keep raw bytes on stdin and sanitize every child failure at this boundary."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_PDF_BYTES:
        raise ValueError(ERROR_MESSAGE)
    if page_number is not None and (type(page_number) is not int or not 1 <= page_number <= 10):
        raise ValueError(ERROR_MESSAGE)
    public = os.environ.get("ACADEMIC_PUBLIC_DEMO") == "1"
    if not public:
        environment = os.environ.copy()
        source_root = str(Path(__file__).resolve().parent.parent)
        environment["PYTHONPATH"] = source_root + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    arguments = [sys.executable, "-m", "academic_assistant.transcript_process"]
    if page_number is not None:
        arguments.append(str(page_number))
    process = None
    job = None
    try:
        if public:
            output = _run_bounded_child(arguments, data, environment=_transcript_child_environment(),
                                        timeout_seconds=EXTRACTION_TIMEOUT_SECONDS,
                                        output_limit=MAX_JSON_BYTES)
            return TranscriptExtraction.model_validate_json(output)
        if os.name == "nt":
            job = _WindowsJob()
        process = subprocess.Popen(
            arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        if job is not None:
            job.assign(process)
        output, _ = process.communicate(input=data, timeout=EXTRACTION_TIMEOUT_SECONDS)
        if process.returncode != 0 or len(output) > MAX_JSON_BYTES:
            raise ValueError(ERROR_MESSAGE)
        return TranscriptExtraction.model_validate_json(output)
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError):
        if process is not None:
            try:
                _terminate_owned(process, job)
            except (OSError, subprocess.SubprocessError):
                pass
        raise ValueError(ERROR_MESSAGE) from None
    finally:
        if job is not None:
            job.close()
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


def _child_main() -> int:
    # This module is trusted code. Input never becomes code or a command argument.
    try:
        if len(sys.argv) > 2:
            return 1
        page_number = int(sys.argv[1]) if len(sys.argv) == 2 else None
        data = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
        if not data or len(data) > MAX_PDF_BYTES:
            return 1
        _apply_public_memory_limit()
        from .transcript_extract import extract_transcript
        result = extract_transcript(data, page_number=page_number)
        output = result.model_dump_json().encode("utf-8")
        if len(output) > MAX_JSON_BYTES:
            return 1
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # No traceback/native exception details may leave this process.
        return 1


if __name__ == "__main__":
    raise SystemExit(_child_main())
