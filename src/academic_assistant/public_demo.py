"""Anonymous demo boundary. Private API/backends never receive a WAN listener.

Budgets are global rather than based on spoofable forwarding headers. Nothing
here stores requests, transcript content, IP addresses or identifiers to disk.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
import math
import os
import re
from threading import Lock
import time
from urllib.parse import parse_qsl, urlsplit

from starlette.responses import JSONResponse

from .progress import accepts_ndjson

_EVIDENCE = re.compile(r"^/v1/academic/evidence/[a-z0-9][a-z0-9._-]{2,127}/preview(?:\.png|\.pdf)?$")
_GET = {"/", "/readyz", "/v1/academic/runtime", "/v1/academic/demo"}
_GET.update(f"/assets/{name}" for name in
            ("app.css", "app.js", "transcript.js", "semantic-ui.js", "evidence.js", "evidence.css"))
_EXAMPLES = {f"/v1/academic/transcripts/examples/{name}" for name in ("early", "near-graduation", "retake")}
_GET.update(path + ".pdf" for path in _EXAMPLES)
_POST = {"/v1/academic/answers", "/v1/academic/chat", "/v1/academic/transcripts/extract",
         "/v1/academic/transcripts/assess", "/v1/academic/transcripts/chat", "/v1/academic/assistant"} | _EXAMPLES
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"}


@dataclass(frozen=True)
class DemoLimits:
    json_bytes: int = 256 * 1024
    pdf_bytes: int = 10 * 1024 * 1024
    response_bytes: int = 16 * 1024 * 1024
    body_seconds: float = 30
    work_seconds: float = 150
    concurrent: int = 4
    work_concurrent: int = 2
    requests_per_minute: int = 120
    posts_per_minute: int = 12
    extracts_per_minute: int = 2
    examples_per_minute: int = 6
    previews_per_minute: int = 12
    inventory_per_minute: int = 6

    def __post_init__(self):
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid demo bound")
            if not name.endswith("seconds") and type(value) is not int:
                raise ValueError("invalid demo bound")


class _Rejected(Exception):
    def __init__(self, status: int):
        self.status = status


def _json_object(data: bytes):
    """Bound nesting before the decoder; reject duplicate keys and non-UTF8."""
    value = data.decode("utf-8")
    quoted = escaped = False
    depth = 0
    for char in value:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 64:
                raise ValueError("invalid JSON")
        elif char in "]}":
            depth -= 1
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("invalid JSON")
            result[key] = item
        return result
    parsed = json.loads(value, object_pairs_hook=unique,
                        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON")))
    if not isinstance(parsed, dict):
        raise ValueError("invalid JSON")


def _accepts_ndjson(scope):
    """Streaming needs an explicit, acceptable media type, never a wildcard."""
    return accepts_ndjson(scope.get("headers", []))


class PublicDemo:
    def __init__(self, backend, limits: DemoLimits | None = None, *, clock=time.monotonic):
        self.backend = backend
        self.limits = limits or DemoLimits()
        self.clock = clock
        self.lock = Lock()
        self.active = self.work_active = 0
        self.events = {name: deque() for name in ("all", "post", "extract", "example", "preview", "inventory")}

    def _enter(self, path, method):
        work = method == "POST" or bool(_EVIDENCE.fullmatch(path)) or path in {"/readyz", "/v1/academic/runtime"}
        budgets = {"all": self.limits.requests_per_minute}
        if method == "POST":
            budgets["post"] = self.limits.posts_per_minute
        if path.endswith("/extract"):
            budgets["extract"] = self.limits.extracts_per_minute
        if path in _EXAMPLES:
            budgets["example"] = self.limits.examples_per_minute
        if _EVIDENCE.fullmatch(path):
            budgets["preview"] = self.limits.previews_per_minute
        if path == "/v1/academic/runtime":
            budgets["inventory"] = self.limits.inventory_per_minute
        with self.lock:
            now = self.clock()
            for name in budgets:
                while self.events[name] and self.events[name][0] <= now - 60:
                    self.events[name].popleft()
            if (self.active >= self.limits.concurrent or work and self.work_active >= self.limits.work_concurrent
                    or any(len(self.events[name]) >= maximum for name, maximum in budgets.items())):
                raise _Rejected(429)
            for name in budgets:
                self.events[name].append(now)
            self.active += 1
            self.work_active += int(work)
        return work

    def _leave(self, work):
        with self.lock:
            self.active -= 1
            self.work_active -= int(work)

    def _validate(self, scope):
        path = scope.get("path", "")
        method = scope.get("method", "")
        raw_path = scope.get("raw_path", path.encode("ascii", "replace"))
        if raw_path != path.encode("ascii", "replace") or len(raw_path) > 512:
            raise _Rejected(404)
        if not (method == "GET" and (path in _GET or _EVIDENCE.fullmatch(path))
                or method == "POST" and path in _POST):
            raise _Rejected(404)
        headers = {}
        pairs = scope.get("headers", [])
        if sum(len(key) + len(value) for key, value in pairs) > 8192:
            raise _Rejected(431)
        for key, value in pairs:
            key = key.lower()
            if key in headers and key in {b"host", b"content-length", b"content-type", b"origin", b"transfer-encoding"}:
                raise _Rejected(400)
            headers[key] = value
        host = headers.get(b"host", b"").decode("ascii", "strict")
        if not (re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?", host)
                or re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", host)):
            raise _Rejected(400)
        if method == "POST":
            origin = headers.get(b"origin")
            if origin:
                target = urlsplit(origin.decode("ascii", "strict"))
                if (target.scheme not in {"http", "https"} or target.netloc != host
                        or target.username or target.password or target.path or target.query or target.fragment):
                    raise _Rejected(403)
            if headers.get(b"sec-fetch-site") == b"cross-site":
                raise _Rejected(403)
            expected = b"application/pdf" if path.endswith("/extract") else b"application/json"
            if headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() != expected:
                raise _Rejected(415)
        query = scope.get("query_string", b"")
        if len(query) > 256:
            raise _Rejected(400)
        values = parse_qsl(query.decode("ascii", "strict"), keep_blank_values=True, strict_parsing=True)
        permitted = {"evidence_index", "pdf_page"} if _EVIDENCE.fullmatch(path) else {"page_number"} if path.endswith("/extract") else set()
        if len({key for key, _ in values}) != len(values) or any(key not in permitted or not re.fullmatch(r"[0-9]{1,4}", value) for key, value in values):
            raise _Rejected(400)
        size = headers.get(b"content-length")
        maximum = self.limits.pdf_bytes if path.endswith("/extract") else self.limits.json_bytes
        if size is not None:
            if not size.isdigit() or len(size) > 9:
                raise _Rejected(400)
            if int(size) > maximum or method == "GET" and int(size):
                raise _Rejected(413)
        return path, method, maximum, int(size) if size is not None else None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.backend(scope, receive, send)
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        work = None
        backend_task = None
        abandoned = False
        response_started = response_closed = client_disconnected = False
        streaming = False
        request_started = self.clock()
        output_lock = asyncio.Lock()

        def stream_error_body():
            elapsed_ms = round(max(0, self.clock() - request_started) * 1000, 3)
            return (json.dumps({"type": "error",
                "message": "요청 처리를 마치지 못했습니다. 다시 시도해 주세요.",
                "elapsed_ms": elapsed_ms}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

        # Reserve terminal-error space before publishing any progress. The
        # backend byte bound includes this error, even on a size breach.
        error_reserve = len(stream_error_body()) + 32

        async def fail_stream():
            nonlocal abandoned, response_closed, client_disconnected
            abandoned = True
            if response_closed or client_disconnected:
                return
            try:
                async with asyncio.timeout(min(self.limits.body_seconds, self.limits.work_seconds)):
                    async with output_lock:
                        if response_closed or client_disconnected:
                            return
                        response_closed = True
                        await send({"type": "http.response.body", "body": stream_error_body(), "more_body": False})
            except Exception:
                # An unavailable/disconnected transport cannot receive an error.
                # It does not cancel the backend or release its work capacity.
                client_disconnected = True

        try:
            path, method, maximum, length = self._validate(scope)
            work = self._enter(path, method)
            if path == "/v1/academic/demo":
                return await JSONResponse({"public": True, "authentication": False,
                    "transcript_storage": "request_memory", "provider": "cloudflare_quick_tunnel"}, headers=_HEADERS)(scope, receive, send)
            body = bytearray()
            if method == "POST":
                try:
                    async with asyncio.timeout(self.limits.body_seconds):
                        while True:
                            event = await receive()
                            if event["type"] == "http.disconnect":
                                return
                            if event["type"] != "http.request":
                                raise _Rejected(400)
                            chunk = event.get("body", b"")
                            if len(body) + len(chunk) > maximum:
                                raise _Rejected(413)
                            body.extend(chunk)
                            if not event.get("more_body", False):
                                break
                except TimeoutError:
                    raise _Rejected(408) from None
                if length is not None and len(body) != length:
                    raise _Rejected(400)
                if not path.endswith("/extract"):
                    _json_object(bytes(body))
            delivered = False
            async def replay():
                nonlocal delivered, client_disconnected, abandoned
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                event = await receive()
                if event["type"] == "http.disconnect" and streaming:
                    client_disconnected = abandoned = True
                return event
            # Only the explicitly negotiated B dialogue stream bypasses whole-
            # response buffering. Student/PDF and ordinary JSON stay buffered.
            stream_requested = (method == "POST" and path == "/v1/academic/assistant"
                                and _accepts_ndjson(scope))
            start = None
            response_body = bytearray()
            response_bytes = 0
            async def collect(event):
                nonlocal start, streaming, response_started, response_closed, response_bytes
                if abandoned:
                    return
                if event["type"] == "http.response.start":
                    if start is not None:
                        raise _Rejected(503)
                    start = event
                    content_type = dict((key.lower(), value) for key, value in event.get("headers", [])).get(b"content-type", b"")
                    streaming = (stream_requested and event["status"] == 200
                                 and content_type.split(b";", 1)[0].strip().lower() == b"application/x-ndjson")
                    if streaming:
                        if self.limits.response_bytes < error_reserve:
                            raise _Rejected(503)
                        public_start = dict(event)
                        public_start["headers"] = [(key, value) for key, value in event.get("headers", [])
                            if key.lower() not in {b"content-length", b"server", b"set-cookie", b"x-accel-buffering"}]
                        public_start["headers"].extend([(b"x-robots-tag", b"noindex, nofollow, noarchive"),
                                                       (b"x-accel-buffering", b"no")])
                        async with output_lock:
                            response_started = True
                            await send(public_start)
                elif event["type"] == "http.response.body":
                    chunk = event.get("body", b"")
                    if streaming:
                        if response_closed:
                            raise _Rejected(503)
                        response_bytes += len(chunk)
                        if response_bytes > self.limits.response_bytes - error_reserve:
                            raise _Rejected(503)
                        response_body.extend(chunk)
                        end = response_body.rfind(b"\n") + 1
                        complete = bytes(response_body[:end])
                        del response_body[:end]
                        final = not event.get("more_body", False)
                        if final and response_body:
                            # A valid final record may omit its trailing newline.
                            # Never publish an unfinished fragment before an error.
                            _json_object(bytes(response_body))
                            complete += bytes(response_body)
                            response_body.clear()
                        if complete or final:
                            async with output_lock:
                                if abandoned:
                                    return
                                response_closed = final
                                await send({"type": "http.response.body", "body": complete, "more_body": not final})
                        return
                    if len(response_body) + len(chunk) > self.limits.response_bytes:
                        raise _Rejected(503)
                    response_body.extend(chunk)
            # Middleware may wait for a shielded sync worker on cancellation.
            # Bound response time independently, retaining its capacity until
            # the existing work finishes. Never enqueue replacements behind it.
            backend_task = asyncio.create_task(self.backend(scope, replay, collect))
            done, _pending = await asyncio.wait({backend_task}, timeout=self.limits.work_seconds)
            if not done:
                abandoned = True
                response_body.clear()
                raise _Rejected(503)
            backend_task.result()
            if streaming:
                if not response_closed and not client_disconnected:
                    await fail_stream()
                return
            if start is None:
                raise _Rejected(503)
            if path == "/readyz":
                if start["status"] != 200 or json.loads(response_body) != {"status": "ready"}:
                    raise _Rejected(503)
                response_body = json.dumps({"status": "ready", "public_demo": True}).encode()
            start = dict(start)
            start["headers"] = [(key, value) for key, value in start.get("headers", [])
                                if key.lower() not in {b"content-length", b"server", b"set-cookie"}]
            start["headers"].append((b"content-length", str(len(response_body)).encode()))
            start["headers"].append((b"x-robots-tag", b"noindex, nofollow, noarchive"))
            await send(start)
            await send({"type": "http.response.body", "body": bytes(response_body)})
        except _Rejected as exc:
            if response_started:
                await fail_stream()
                return
            headers = dict(_HEADERS)
            if exc.status == 429:
                headers["Retry-After"] = "60"
            await JSONResponse({"detail": {429: "demo busy; retry later", 408: "request timeout"}.get(exc.status, "request unavailable")},
                               status_code=exc.status, headers=headers)(scope, receive, send)
        except (ValueError, UnicodeError, RecursionError):
            if response_started:
                await fail_stream()
                return
            await JSONResponse({"detail": "invalid request"}, status_code=422, headers=_HEADERS)(scope, receive, send)
        except Exception:
            if response_started:
                await fail_stream()
                return
            await JSONResponse({"detail": "service unavailable"}, status_code=503, headers=_HEADERS)(scope, receive, send)
        finally:
            if work is not None:
                if backend_task is not None and not backend_task.done():
                    abandoned = True
                    def finished(task):
                        try:
                            task.result()
                        except BaseException:
                            pass
                        finally:
                            self._leave(work)
                    backend_task.add_done_callback(finished)
                else:
                    self._leave(work)


def create_demo_app():
    if os.environ.get("ACADEMIC_PUBLIC_DEMO") != "1":
        raise RuntimeError("public demo profile must be explicitly selected")
    from .api import app
    return PublicDemo(app)
