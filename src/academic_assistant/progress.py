"""Request-local measured progress; no request storage or provider secrets."""
from contextlib import contextmanager
from contextvars import ContextVar
import time
import uuid
import math

_current = ContextVar("academic_progress", default=None)


def accepts_ndjson(headers):
    """An explicit media type with valid positive q, never a substring."""
    for key, value in headers:
        if key.lower() != b"accept":
            continue
        for item in value.lower().split(b","):
            parts = item.split(b";")
            if parts[0].strip() != b"application/x-ndjson":
                continue
            qualities = [parameter.partition(b"=")[2].strip() for parameter in parts[1:]
                         if parameter.partition(b"=")[0].strip() == b"q"]
            if len(qualities) > 1:
                continue
            try:
                quality = float(qualities[0]) if qualities else 1.0
            except ValueError:
                continue
            if math.isfinite(quality) and 0 < quality <= 1:
                return True
    return False


class ProgressReporter:
    def __init__(self, sink, *, clock=time.monotonic, request_id=None):
        self.sink = sink
        self.clock = clock
        self.started = clock()
        self.request_id = request_id or uuid.uuid4().hex
        self.sequence = 0

    @property
    def elapsed_ms(self):
        return max(0.0, (self.clock() - self.started) * 1000)

    def emit(self, stage, state, message, *, details=None, duration_ms=None, explanation_source="system"):
        self.sequence += 1
        event = dict(schema_version="1.0.0", type="progress", request_id=self.request_id,
                     sequence=self.sequence, stage=stage, state=state,
                     elapsed_ms=self.elapsed_ms, duration_ms=duration_ms,
                     message=message, explanation_source=explanation_source, details=details or {})
        self.sink(event)
        return event


@contextmanager
def progress_scope(reporter):
    token = _current.set(reporter)
    try:
        yield
    finally:
        _current.reset(token)


def emit(stage, state, message, **kwargs):
    reporter = _current.get()
    if reporter is not None:
        return reporter.emit(stage, state, message, **kwargs)


@contextmanager
def step(stage, message, *, details=None, explanation_source="system"):
    started = time.monotonic()
    emit(stage, "started", message, details=details, explanation_source=explanation_source)
    try:
        yield
    except Exception:
        emit(stage, "failed", "이 단계를 완료하지 못했습니다.", details={**(details or {}), "error_code": stage + "_failed"},
             duration_ms=max(0.0, (time.monotonic() - started) * 1000))
        raise
    else:
        emit(stage, "completed", message, details=details, explanation_source=explanation_source,
             duration_ms=max(0.0, (time.monotonic() - started) * 1000))
