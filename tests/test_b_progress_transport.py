"""Synthetic ASGI progress transport; no server, tunnel, model, or graph calls."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import threading
import time
import unittest

from academic_assistant.public_demo import DemoLimits, PublicDemo


PATH = "/v1/academic/assistant"
NDJSON = b"application/x-ndjson; charset=utf-8"


def request_scope(path=PATH, method="POST", accept=b"application/x-ndjson"):
    headers = [(b"host", b"127.0.0.1:8765")]
    if method == "POST":
        headers.append((b"content-type", b"application/json"))
    if accept is not None:
        headers.append((b"accept", accept))
    return {"type": "http", "path": path, "raw_path": path.encode(), "method": method,
            "query_string": b"", "headers": headers, "http_version": "1.1",
            "scheme": "http", "server": ("127.0.0.1", 8765)}


def start(status=200, content_type=NDJSON):
    return {"type": "http.response.start", "status": status,
            "headers": [(b"content-type", content_type), (b"cache-control", b"no-store"),
                        (b"content-length", b"9999"), (b"server", b"private-server-marker"),
                        (b"set-cookie", b"private-cookie-marker"), (b"x-accel-buffering", b"yes")]}


def row(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


PROGRESS = row({"schema_version": "1.0.0", "type": "progress", "request_id": "synthetic",
    "sequence": 1, "stage": "received", "state": "started", "elapsed_ms": 0,
    "duration_ms": None, "message": "질문을 접수했습니다.", "explanation_source": "system", "details": {}})
RESULT = row({"type": "result", "response": {"answer": "synthetic result"}, "elapsed_ms": 1})


class Exchange:
    def __init__(self):
        self.sent = []
        self.disconnected = asyncio.Event()
        self.received = False

    async def receive(self):
        if not self.received:
            self.received = True
            return {"type": "http.request", "body": b"{}", "more_body": False}
        await self.disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(self, event):
        self.sent.append(dict(event))

    def body(self):
        return b"".join(event.get("body", b"") for event in self.sent)

    def records(self):
        return [json.loads(line) for line in self.body().splitlines() if line]

    def starts(self):
        return [event for event in self.sent if event["type"] == "http.response.start"]


class ProgressTransportTests(unittest.IsolatedAsyncioTestCase):
    async def wait(self, event):
        await asyncio.wait_for(event.wait(), timeout=1)

    async def capacity_released(self, app):
        async with asyncio.timeout(1):
            while app.active or app.work_active:
                await asyncio.sleep(.001)

    def assert_stream_error(self, exchange, expected_progress=1):
        self.assertEqual(1, len(exchange.starts()), "An accepted stream must never start a second response")
        self.assertEqual(200, exchange.starts()[0]["status"])
        records = exchange.records()
        self.assertEqual(expected_progress + 1, len(records))
        self.assertEqual(["progress"] * expected_progress + ["error"], [item["type"] for item in records])
        self.assertEqual({"type", "message", "elapsed_ms"}, set(records[-1]))
        self.assertIsInstance(records[-1]["elapsed_ms"], (int, float))
        self.assertGreaterEqual(records[-1]["elapsed_ms"], 0)
        self.assertFalse(exchange.sent[-1].get("more_body", False))
        for marker in (b"SECRET", b"private-path", b"private-server-marker", b"private-cookie-marker"):
            self.assertNotIn(marker, exchange.body())
        return records[-1]

    async def test_first_progress_arrives_before_backend_finishes_and_headers_are_stripped(self):
        first, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})
            first.set()
            await release.wait()
            await send({"type": "http.response.body", "body": RESULT, "more_body": False})
            finished.set()

        app, exchange = PublicDemo(backend), Exchange()
        task = asyncio.create_task(app(request_scope(), exchange.receive, exchange.send))
        try:
            await self.wait(first)
            self.assertFalse(task.done())
            self.assertFalse(finished.is_set())
            self.assertEqual(PROGRESS, exchange.body(), "Actual ASGI send must publish progress before completion")
            self.assertEqual((1, 1), (app.active, app.work_active))
            headers = dict(exchange.starts()[0]["headers"])
            for name in (b"content-length", b"server", b"set-cookie"):
                self.assertNotIn(name, headers)
            self.assertEqual(b"no", headers[b"x-accel-buffering"])
            self.assertEqual(b"no-store", headers[b"cache-control"])
            release.set()
            await task
            self.assertEqual(PROGRESS + RESULT, exchange.body())
            self.assertTrue(finished.is_set())
            self.assertEqual((0, 0), (app.active, app.work_active))
        finally:
            release.set()
            await task

    async def test_split_utf8_records_publish_only_complete_lines_and_final_without_newline(self):
        fragment, release, first, finish = (asyncio.Event() for _ in range(4))
        split = next(index for index, byte in enumerate(PROGRESS) if byte >= 0xe0) + 1

        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS[:split], "more_body": True})
            fragment.set()
            await release.wait()
            await send({"type": "http.response.body", "body": PROGRESS[split:], "more_body": True})
            first.set()
            await finish.wait()
            await send({"type": "http.response.body", "body": RESULT.rstrip(b"\n"), "more_body": False})

        app, exchange = PublicDemo(backend), Exchange()
        task = asyncio.create_task(app(request_scope(), exchange.receive, exchange.send))
        try:
            await self.wait(fragment)
            self.assertEqual(b"", exchange.body())
            release.set()
            await self.wait(first)
            self.assertEqual(PROGRESS, exchange.body())
            finish.set()
            await task
            self.assertEqual(["progress", "result"], [item["type"] for item in exchange.records()])
        finally:
            release.set()
            finish.set()
            await task

    async def test_only_explicit_same_post_200_ndjson_bypasses_buffering(self):
        cases = [(PATH, "POST", accept, 200, NDJSON) for accept in
                 (None, b"application/json", b"*/*", b"application/x-ndjson-ish",
                  b"text/plain;format=application/x-ndjson", b"application/x-ndjson;q=0",
                  b"application/x-ndjson;q=-0.1", b"application/x-ndjson;q=1.1",
                  b"application/x-ndjson;q=nan", b"application/x-ndjson;q=inf",
                  b"application/x-ndjson;q=invalid", b"application/x-ndjson;q=",
                  b"application/x-ndjson;q", b"application/x-ndjson;q=0;q=1",
                  b"application/x-ndjson;q=1;q=1", b"application/x-ndjson;q=invalid;q=0.5")]
        cases += [(PATH, "POST", b"application/x-ndjson", 200, b"application/json"),
                  (PATH, "POST", b"application/x-ndjson", 422, NDJSON),
                  ("/v1/academic/transcripts/assess", "POST", b"application/x-ndjson", 200, NDJSON),
                  ("/", "GET", b"application/x-ndjson", 200, NDJSON),
                  ("/v1/academic/evidence/cwnu.fixture/preview.pdf", "GET", b"application/x-ndjson", 200, b"application/pdf")]
        for path, method, accept, status, content_type in cases:
            with self.subTest(path=path, method=method, accept=accept, status=status, media=content_type):
                collected, release = asyncio.Event(), asyncio.Event()

                async def backend(scope, receive, send):
                    if scope["method"] == "POST":
                        await receive()
                    await send(start(status, content_type))
                    await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})
                    collected.set()
                    await release.wait()
                    await send({"type": "http.response.body", "body": RESULT, "more_body": False})

                app, exchange = PublicDemo(backend), Exchange()
                task = asyncio.create_task(app(request_scope(path, method, accept), exchange.receive, exchange.send))
                try:
                    await self.wait(collected)
                    self.assertEqual([], exchange.sent, "Unnegotiated/other endpoint/media responses remain fully buffered")
                    release.set()
                    await task
                    self.assertEqual(PROGRESS + RESULT, exchange.body())
                    self.assertEqual(status, exchange.starts()[0]["status"])
                    self.assertEqual(str(len(PROGRESS + RESULT)).encode(), dict(exchange.starts()[0]["headers"])[b"content-length"])
                finally:
                    release.set()
                    await task

    async def test_valid_weighted_accept_is_explicit_and_does_not_change_body(self):
        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS + RESULT, "more_body": False})

        for accept in (b"application/x-ndjson", b"application/x-ndjson;q=1",
                       b"application/json, Application/X-NDJSON; q=0.5",
                       b"application/x-ndjson; charset=utf-8; Q = 0.5",
                       b"application/x-ndjson;q=0;q=1, application/x-ndjson;q=0.5"):
            with self.subTest(accept=accept):
                exchange = Exchange()
                await PublicDemo(backend)(request_scope(accept=accept), exchange.receive, exchange.send)
                self.assertEqual(PROGRESS + RESULT, exchange.body())
                self.assertNotIn(b"content-length", dict(exchange.starts()[0]["headers"]))

    async def test_after_start_failure_is_one_sanitized_ndjson_error_with_actual_elapsed(self):
        clock = [20.0]

        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})
            clock[0] += 1.25
            raise RuntimeError("SECRET private-path")

        app, exchange = PublicDemo(backend, clock=lambda: clock[0]), Exchange()
        await app(request_scope(), exchange.receive, exchange.send)
        error = self.assert_stream_error(exchange)
        self.assertEqual(1250, error["elapsed_ms"])
        self.assertEqual((0, 0), (app.active, app.work_active))

    async def test_timeout_discards_partial_record_and_retains_capacity_until_actual_completion(self):
        release, finished = asyncio.Event(), asyncio.Event()

        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})
            await send({"type": "http.response.body", "body": b'{"type":"progress","unfinished":', "more_body": True})
            await release.wait()
            await send({"type": "http.response.body", "body": RESULT, "more_body": False})
            finished.set()

        app = PublicDemo(backend, replace(DemoLimits(), work_seconds=.02, work_concurrent=1))
        exchange = Exchange()
        try:
            before = time.monotonic()
            await app(request_scope(), exchange.receive, exchange.send)
            self.assertLess(time.monotonic() - before, .3)
            error = self.assert_stream_error(exchange)
            self.assertGreaterEqual(error["elapsed_ms"], 15)
            self.assertNotIn(b"unfinished", exchange.body())
            self.assertFalse(finished.is_set())
            self.assertEqual((1, 1), (app.active, app.work_active))
            blocked = Exchange()
            await app(request_scope(), blocked.receive, blocked.send)
            self.assertEqual(429, blocked.starts()[0]["status"])
            count = len(exchange.sent)
            release.set()
            await self.capacity_released(app)
            self.assertTrue(finished.is_set())
            self.assertEqual(count, len(exchange.sent), "Late producer output cannot follow the terminal error")
        finally:
            release.set()
            await self.capacity_released(app)

    async def test_size_breach_reserves_error_budget_and_still_holds_worker_capacity(self):
        release, failed, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})
            try:
                await send({"type": "http.response.body", "body": b"SECRET private-path" * 1000, "more_body": True})
            finally:
                failed.set()
                await release.wait()
                finished.set()

        app = PublicDemo(backend, replace(DemoLimits(), response_bytes=1024, work_seconds=.02, work_concurrent=1))
        exchange = Exchange()
        try:
            await app(request_scope(), exchange.receive, exchange.send)
            self.assertTrue(failed.is_set())
            self.assertFalse(finished.is_set())
            self.assert_stream_error(exchange)
            self.assertLessEqual(len(exchange.body()), 1024)
            self.assertEqual((1, 1), (app.active, app.work_active))
            count = len(exchange.sent)
            release.set()
            await self.capacity_released(app)
            self.assertTrue(finished.is_set())
            self.assertEqual(count, len(exchange.sent))
        finally:
            release.set()
            await self.capacity_released(app)

    async def test_too_small_stream_budget_rejects_before_http_stream_start(self):
        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS, "more_body": False})

        exchange = Exchange()
        await PublicDemo(backend, replace(DemoLimits(), response_bytes=1))(request_scope(), exchange.receive, exchange.send)
        self.assertEqual(1, len(exchange.starts()))
        self.assertEqual(503, exchange.starts()[0]["status"])
        self.assertEqual({"detail": "request unavailable"}, json.loads(exchange.body()))
        self.assertNotIn(PROGRESS, exchange.body())

    async def test_duplicate_start_missing_final_and_invalid_tail_emit_no_second_start(self):
        for failure in ("duplicate", "missing-final", "bad-tail"):
            with self.subTest(failure=failure):
                async def backend(scope, receive, send):
                    await receive()
                    await send(start())
                    await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})
                    if failure == "duplicate":
                        await send(start())
                    if failure == "bad-tail":
                        await send({"type": "http.response.body", "body": b'{"private-path":', "more_body": False})

                exchange = Exchange()
                await PublicDemo(backend)(request_scope(), exchange.receive, exchange.send)
                self.assert_stream_error(exchange)

    async def test_disconnect_keeps_real_thread_capacity_and_suppresses_late_output(self):
        release, finished = threading.Event(), threading.Event()
        first = asyncio.Event()

        def work():
            release.wait(timeout=1)
            finished.set()

        async def backend(scope, receive, send):
            await receive()
            worker = asyncio.create_task(asyncio.to_thread(work))
            try:
                await send(start())
                await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})
                first.set()
                self.assertEqual("http.disconnect", (await receive())["type"])
            finally:
                await asyncio.shield(worker)
            await send({"type": "http.response.body", "body": RESULT, "more_body": False})

        app = PublicDemo(backend, replace(DemoLimits(), work_seconds=.03, work_concurrent=1))
        exchange = Exchange()
        task = asyncio.create_task(app(request_scope(), exchange.receive, exchange.send))
        try:
            await self.wait(first)
            exchange.disconnected.set()
            await task
            self.assertFalse(finished.is_set())
            self.assertEqual(PROGRESS, exchange.body())
            self.assertEqual((1, 1), (app.active, app.work_active))
            blocked = Exchange()
            await app(request_scope(), blocked.receive, blocked.send)
            self.assertEqual(429, blocked.starts()[0]["status"])
            count = len(exchange.sent)
            release.set()
            await self.capacity_released(app)
            self.assertTrue(finished.is_set())
            self.assertEqual(count, len(exchange.sent))
        finally:
            release.set()
            await task
            await self.capacity_released(app)

    async def test_outer_request_cancel_does_not_cancel_work_or_free_its_slot(self):
        first, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})
            first.set()
            await release.wait()
            finished.set()
            await send({"type": "http.response.body", "body": RESULT, "more_body": False})

        app, exchange = PublicDemo(backend), Exchange()
        task = asyncio.create_task(app(request_scope(), exchange.receive, exchange.send))
        try:
            await self.wait(first)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(finished.is_set())
            self.assertEqual((1, 1), (app.active, app.work_active))
            count = len(exchange.sent)
            release.set()
            await self.capacity_released(app)
            self.assertTrue(finished.is_set())
            self.assertEqual(count, len(exchange.sent))
        finally:
            release.set()
            await self.capacity_released(app)

    async def test_pdf_failure_keeps_full_buffering_and_sanitized_json_boundary(self):
        async def backend(scope, receive, send):
            await send(start(content_type=b"application/pdf"))
            await send({"type": "http.response.body", "body": b"%PDF-synthetic SECRET", "more_body": True})
            raise RuntimeError("private-path")

        exchange = Exchange()
        await PublicDemo(backend)(request_scope("/v1/academic/evidence/cwnu.fixture/preview.pdf", "GET"), exchange.receive, exchange.send)
        self.assertEqual(1, len(exchange.starts()))
        self.assertEqual(503, exchange.starts()[0]["status"])
        self.assertEqual({"detail": "service unavailable"}, json.loads(exchange.body()))
        self.assertNotIn(b"%PDF", exchange.body())
        self.assertNotIn(b"SECRET", exchange.body())

    async def test_downstream_send_failure_never_restarts_http_or_echoes_exception(self):
        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS, "more_body": True})

        for failure in ("start", "body", "disconnected"):
            with self.subTest(failure=failure):
                exchange, attempts = Exchange(), []

                async def send(event):
                    attempts.append(dict(event))
                    if (failure == "start" and event["type"] == "http.response.start"
                            or failure == "body" and event["type"] == "http.response.body" and event.get("more_body")
                            or failure == "disconnected" and event["type"] == "http.response.body"):
                        raise OSError("SECRET private-path")
                    await exchange.send(event)

                app = PublicDemo(backend)
                await app(request_scope(), exchange.receive, send)
                self.assertEqual(1, sum(event["type"] == "http.response.start" for event in attempts))
                bodies = [event["body"] for event in attempts if event["type"] == "http.response.body"]
                self.assertEqual("error", json.loads(bodies[-1])["type"])
                self.assertNotIn(b"SECRET", bodies[-1])
                self.assertNotIn(b"private-path", bodies[-1])
                self.assertEqual((0, 0), (app.active, app.work_active))

    async def test_failure_after_closed_stream_cannot_append_another_body(self):
        async def backend(scope, receive, send):
            await receive()
            await send(start())
            await send({"type": "http.response.body", "body": PROGRESS + RESULT, "more_body": False})
            raise RuntimeError("SECRET private-path")

        app, exchange = PublicDemo(backend), Exchange()
        await app(request_scope(), exchange.receive, exchange.send)
        self.assertEqual(1, len(exchange.starts()))
        self.assertEqual(PROGRESS + RESULT, exchange.body())
        self.assertEqual(2, len(exchange.sent))
        self.assertEqual((0, 0), (app.active, app.work_active))


if __name__ == "__main__":
    unittest.main()
