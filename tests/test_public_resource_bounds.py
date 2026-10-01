"""Public limits use synthetic documents and owned harmless processes only."""
from __future__ import annotations

import asyncio
import ctypes
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pypdfium2 as pdfium

from academic_assistant import api, evidence_process, transcript_process
from academic_assistant.evidence_pdf import EvidencePdfService, create_evidence_router
from academic_assistant.transcript_models import TranscriptExtraction
from tests.test_evidence_pdf import RULE, SOURCE, pdf_bytes, red_pixels, registry_for


class PublicUploadDeadlineTests(unittest.TestCase):
    def test_trickled_upload_hits_absolute_deadline_and_releases_slot(self):
        extracted = TranscriptExtraction(courses=[], issues=["확인 필요"], extraction_method="manual_required")

        async def exercise():
            async def trickle():
                for _ in range(20):
                    await asyncio.sleep(0.02)
                    yield b"%PDF-small-chunk"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                                         base_url="http://test") as client:
                started = time.monotonic()
                timeout = await client.post("/v1/academic/transcripts/extract", content=trickle(),
                                            headers={"Content-Type": "application/pdf"})
                self.assertEqual(408, timeout.status_code)
                self.assertEqual({"detail": "service unavailable"}, timeout.json())
                self.assertLess(time.monotonic() - started, 0.3)
                recovery = await client.post("/v1/academic/transcripts/extract", content=b"%PDF-small",
                                             headers={"Content-Type": "application/pdf"})
                self.assertEqual(200, recovery.status_code)

        with patch.object(api, "UPLOAD_RECEIVE_TIMEOUT_SECONDS", 0.05), \
                patch.object(transcript_process, "extract_isolated", return_value=extracted) as extract:
            asyncio.run(exercise())
        extract.assert_called_once_with(b"%PDF-small", page_number=None)


class PublicEvidenceIsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="public-evidence-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = pdf_bytes([[('Approved graduation requirement', 50, 650)]])
        self.source = self.root / "approved.pdf"
        self.source.write_bytes(self.data)
        self.registry = registry_for(self.data)
        self.mapping = self.root / "source-map.json"
        self.mapping.write_text(json.dumps({SOURCE: str(self.source)}), encoding="utf-8")
        self.env = {"ACADEMIC_SOURCE_MAP": str(self.mapping), "ACADEMIC_PUBLIC_DEMO": "1"}

    def client(self, public=True):
        app = FastAPI()
        env = self.env if public else {"ACADEMIC_SOURCE_MAP": str(self.mapping)}
        app.include_router(create_evidence_router(lambda: SimpleNamespace(registry=self.registry), environ=env))
        return TestClient(app)

    def test_actual_child_preserves_exact_redline_pages_and_original(self):
        client = self.client()
        with patch.object(evidence_process, "_run_bounded_child", wraps=evidence_process._run_bounded_child) as launch:
            metadata = client.get(f"/v1/academic/evidence/{RULE}/preview")
        self.assertEqual(200, metadata.status_code)
        self.assertEqual("exact", metadata.json()["precision"])
        self.assertEqual(20, launch.call_args.kwargs["timeout_seconds"])
        self.assertEqual(16 * 1024 * 1024, launch.call_args.kwargs["output_limit"])
        image = client.get(metadata.json()["image_url"])
        self.assertEqual(200, image.status_code)
        self.assertTrue(red_pixels(image.content))
        download = client.get(metadata.json()["pdf_url"])
        self.assertEqual(200, download.status_code)
        with pdfium.PdfDocument(download.content) as document:
            self.assertEqual(1, len(document))
        self.assertEqual(self.data, self.source.read_bytes())
        self.assertNotIn(str(self.source), metadata.text)

    def test_selected_secondary_evidence_and_fallback_stay_approved(self):
        self.registry.rules[RULE]["evidence"].append({**self.registry.rules[RULE]["evidence"][0],
                                                    "evidence_type": "department_confirmation"})
        response = self.client().get(f"/v1/academic/evidence/{RULE}/preview?evidence_index=1")
        self.assertEqual(200, response.status_code)
        self.assertEqual("page_only", response.json()["precision"])
        self.assertIn("evidence_index=1", response.json()["image_url"])
        image = self.client().get(response.json()["image_url"])
        self.assertEqual([], red_pixels(image.content))
        self.assertEqual(404, self.client().get(f"/v1/academic/evidence/{RULE}/preview?pdf_page=2").status_code)

    def test_child_input_and_environment_exclude_private_server_values(self):
        self.registry.rules[RULE]["private_config"] = "private-extra-marker"
        self.registry.sources[SOURCE]["private_config"] = "private-extra-marker"
        self.registry.sources[SOURCE]["review"]["reviewer_note"] = "private-extra-marker"
        service = EvidencePdfService.from_env(self.registry, self.env)
        with patch.dict(os.environ, {"NEO4J_PASSWORD": "credential-marker", "ACADEMIC_LLM_URL": "llm-marker",
                                     "PYTHONPATH": "private-extra-marker"}), \
                patch.object(evidence_process, "_run_bounded_child", wraps=evidence_process._run_bounded_child) as launch:
            evidence_process.preview_isolated(service, RULE)
        self.assertNotIn(b"private-extra-marker", launch.call_args.args[1])
        environment = launch.call_args.kwargs["environment"]
        self.assertNotIn("NEO4J_PASSWORD", environment)
        self.assertNotIn("ACADEMIC_LLM_URL", environment)
        self.assertNotIn("credential-marker", str(environment))
        request = json.loads(launch.call_args.args[1])
        self.assertEqual([SOURCE], [request["source"]["source_id"]])
        self.assertEqual(1, len(request["rule"]["evidence"]))

    def test_tampering_child_failure_and_bad_output_are_sanitized_and_slot_recovers(self):
        client = self.client()
        self.source.write_bytes(self.data + b"tampered")
        self.assertEqual(503, client.get(f"/v1/academic/evidence/{RULE}/preview").status_code)
        self.source.write_bytes(self.data)
        for failure in (subprocess.TimeoutExpired("private-path-marker", 20),
                        OSError("private-path-marker"), ValueError("private-path-marker")):
            with patch.object(evidence_process, "_run_bounded_child", side_effect=failure):
                response = client.get(f"/v1/academic/evidence/{RULE}/preview")
                self.assertEqual(503, response.status_code)
                self.assertEqual({"detail": "evidence preview unavailable"}, response.json())
                self.assertNotIn("private-path-marker", response.text)
        with patch.object(evidence_process, "_run_bounded_child", return_value=b'{"private":"path"}'):
            self.assertEqual(503, client.get(f"/v1/academic/evidence/{RULE}/preview").status_code)
        self.assertEqual(200, client.get(f"/v1/academic/evidence/{RULE}/preview").status_code)

    def test_private_preview_keeps_existing_in_process_behavior(self):
        with patch.object(evidence_process, "preview_isolated", side_effect=AssertionError("public child called")):
            self.assertEqual(200, self.client(public=False).get(f"/v1/academic/evidence/{RULE}/preview").status_code)

    def test_child_cannot_echo_a_private_notice_or_rendered_text(self):
        service = EvidencePdfService.from_env(self.registry, self.env)
        metadata, _ = service.preview(RULE)
        for changes in ({"notice": "private-path-marker"}, {"source_sha256": "0" * 64}):
            output = json.dumps({"metadata": {**metadata.model_dump(), **changes}, "rendered": None}).encode()
            with patch.object(evidence_process, "_run_bounded_child", return_value=output):
                response = self.client().get(f"/v1/academic/evidence/{RULE}/preview")
                self.assertEqual(503, response.status_code)
                self.assertNotIn("private-path-marker", response.text)
        output = json.dumps({"metadata": metadata.model_dump(), "rendered": "cHJpdmF0ZS1wYXRoLW1hcmtlcg=="}).encode()
        with patch.object(evidence_process, "_run_bounded_child", return_value=output):
            response = self.client().get(f"/v1/academic/evidence/{RULE}/preview.png")
            self.assertEqual(503, response.status_code)
            self.assertNotIn("private-path-marker", response.text)


class PublicNativeProcessBoundsTests(unittest.TestCase):
    def test_actual_public_transcript_child(self):
        document = pdfium.PdfDocument.new()
        try:
            document.new_page(100, 100).close()
            output = BytesIO()
            document.save(output)
        finally:
            document.close()
        with patch.dict(os.environ, {"ACADEMIC_PUBLIC_DEMO": "1"}):
            result = transcript_process.extract_isolated(output.getvalue())
        self.assertEqual("manual_required", result.extraction_method)
        self.assertEqual([], result.courses)

    def test_actual_child_cannot_allocate_more_than_768_mib(self):
        workload = (
            "import sys\nfrom academic_assistant.transcript_process import _apply_public_memory_limit\n"
            "sys.stdin.buffer.read()\n_apply_public_memory_limit()\n"
            "try:\n allocation=bytearray(832*1024*1024)\n print('unexpected-allocation',flush=True)\n"
            "except MemoryError:\n print('memory-limited',flush=True)\n"
        )
        output = transcript_process._run_bounded_child([sys.executable, "-c", workload], b"safe",
            environment=transcript_process._preview_child_environment(), timeout_seconds=5, output_limit=1024)
        self.assertEqual(b"memory-limited", output.strip())

    def owned_failure(self, mode):
        hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden)
        temporary = tempfile.TemporaryDirectory(prefix="public-owned-process-test-")
        pid_path = Path(temporary.name) / "owned.pid"
        workload = (
            "import os,subprocess,sys,time\nfrom pathlib import Path\nsys.stdin.buffer.read()\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)'],"
            "creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)\n"
            f"Path({str(pid_path)!r}).write_text(str(child.pid),encoding='ascii')\n"
            + ("time.sleep(5)\n" if mode == "timeout" else "sys.stdout.buffer.write(b'x'*100000); sys.stdout.buffer.flush(); time.sleep(5)\n")
        )
        kernel = None
        handle = None
        pid = None
        try:
            started = time.monotonic()
            with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                transcript_process._run_bounded_child([sys.executable, "-c", workload], b"safe",
                    environment=transcript_process._preview_child_environment(), timeout_seconds=0.4, output_limit=1024)
            self.assertLess(time.monotonic() - started, 2)
            pid = int(pid_path.read_text(encoding="ascii"))
            if os.name == "nt":
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.WaitForSingleObject.restype = wintypes.DWORD
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x00100000, False, pid)
                if handle:
                    self.assertEqual(0, kernel.WaitForSingleObject(handle, 1000), "owned descendant survived")
            else:
                status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                                        text=True, timeout=1, check=False).stdout.strip()
                self.assertTrue(not status or status.startswith("Z"), "owned descendant survived")
            self.assertIsNone(sentinel.poll(), "unrelated process was terminated")
        finally:
            if handle:
                kernel.CloseHandle(handle)
            if os.name != "nt" and pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if sentinel.poll() is None:
                sentinel.kill()
            sentinel.wait(timeout=3)
            temporary.cleanup()

    def test_actual_timeout_kills_owned_descendants_and_preserves_unrelated_process(self):
        self.owned_failure("timeout")

    def test_actual_excess_stdout_kills_owned_descendants_and_preserves_unrelated_process(self):
        self.owned_failure("output")


class PublicTranscriptEnvironmentTests(unittest.TestCase):
    def test_poisoned_server_environment_is_absent_in_actual_pdf_and_ocr_children(self):
        secret_keys = ("NEO4J_PASSWORD", "NEO4J_URI", "ACADEMIC_LLM_URL", "ACADEMIC_LLM_API_KEY",
                       "OPENAI_API_KEY", "CLOUDFLARE_API_TOKEN", "TUNNEL_TOKEN", "SSH_AUTH_SOCK",
                       "GMAIL_TOKEN", "CUSTOM_SERVICE_SECRET", "PYTHONHOME", "PYTHONSTARTUP")
        poison = {key: "synthetic-private-marker" for key in secret_keys}
        poison.update({"ACADEMIC_PUBLIC_DEMO": "1", "PYTHONPATH": "synthetic-private-marker",
                       "PATH": "synthetic-private-marker", "PATHEXT": ".BAT"})
        document = pdfium.PdfDocument.new()
        try:
            document.new_page(100, 100).close()
            stream = BytesIO()
            document.save(stream)
        finally:
            document.close()
        workload = (
            "import os,sys\nfrom pathlib import Path\n"
            f"secret_keys={secret_keys!r}\n"
            "assert all(key not in os.environ for key in secret_keys)\n"
            "assert 'synthetic-private-marker' not in os.environ.get('PYTHONPATH','')\n"
            "assert 'synthetic-private-marker' not in os.environ.get('PATH','')\n"
            "from academic_assistant import transcript_extract as extraction\n"
            "from academic_assistant.transcript_process import _child_main\n"
            "native_run=extraction.subprocess.run\nocr_launches=[]\n"
            "def observed_run(arguments,**options):\n"
            " assert all(key not in os.environ for key in secret_keys)\n"
            " actual=Path(arguments[0])\n"
            " expected=Path(os.environ['PATH'].split(os.pathsep)[0])/'powershell.exe'\n"
            " assert actual.is_absolute() and actual.resolve()==expected.resolve()\n"
            " assert os.environ['NoDefaultCurrentDirectoryInExePath']=='1'\n"
            " ocr_launches.append(True)\n return native_run(arguments,**options)\n"
            "extraction.subprocess.run=observed_run\n"
            "code=_child_main()\nassert code==0\n"
            "if os.name=='nt':\n assert ocr_launches\n"
            "raise SystemExit(code)\n"
        )
        native_start = subprocess.Popen
        captured = {}
        with tempfile.TemporaryDirectory(prefix="untrusted-powershell-test-") as temporary:
            # A harmless invalid executable must never shadow the OS PowerShell.
            Path(temporary, "powershell.exe").write_bytes(b"synthetic invalid executable")

            def launch(_arguments, **options):
                captured.update(options["env"])
                return native_start([sys.executable, "-c", workload], cwd=temporary, **options)

            with patch.dict(os.environ, poison), patch.object(transcript_process.subprocess, "Popen", side_effect=launch):
                result = transcript_process.extract_isolated(stream.getvalue())
        self.assertEqual("manual_required", result.extraction_method)
        self.assertEqual([], result.courses)
        self.assertTrue(all(key not in captured for key in secret_keys))
        source_root = Path(transcript_process.__file__).resolve().parent.parent
        paths = captured["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(str(source_root), paths[0])
        if os.name == "nt":
            self.assertEqual([str(source_root), str(source_root.parent / ".venv" / "Lib" / "site-packages")], paths)
        if os.name == "nt":
            self.assertEqual(".EXE", captured["PATHEXT"])
            self.assertEqual("1", captured["NoDefaultCurrentDirectoryInExePath"])

    def test_private_child_retains_operator_environment_and_pythonpath(self):
        result = TranscriptExtraction(courses=[], issues=["확인 필요"], extraction_method="manual_required")
        child = MagicMock()
        child.returncode = 0
        child.communicate.return_value = (result.model_dump_json().encode(), None)
        with patch.dict(os.environ, {"ACADEMIC_PUBLIC_DEMO": "0", "NEO4J_PASSWORD": "synthetic-secret",
                                     "PYTHONPATH": "operator-pythonpath", "PATH": "operator-path"}), \
                patch.object(transcript_process, "_WindowsJob"), \
                patch.object(transcript_process, "_run_bounded_child", side_effect=AssertionError("public child used")), \
                patch.object(transcript_process.subprocess, "Popen", return_value=child) as launch:
            transcript_process.extract_isolated(b"%PDF-synthetic")
        environment = launch.call_args.kwargs["env"]
        self.assertEqual("synthetic-secret", environment["NEO4J_PASSWORD"])
        self.assertEqual("operator-path", environment["PATH"])
        self.assertTrue(environment["PYTHONPATH"].endswith(os.pathsep + "operator-pythonpath"))

    @unittest.skipUnless(os.name == "nt", "Windows launcher uses the base executable")
    def test_actual_base_runtime_keeps_pinned_dependencies_for_both_scrubbed_children(self):
        from scripts.operations import prototype_supervisor as supervisor

        source_root = Path(transcript_process.__file__).resolve().parent.parent
        config = {"ACADEMIC_EVIDENCE_BACKEND": "neo4j", "NEO4J_URI": "bolt://127.0.0.1:7687",
                  "NEO4J_DATABASE": "neo4j", "NEO4J_USER": "synthetic-user",
                  "NEO4J_PASSWORD": "synthetic-secret", "ACADEMIC_LLM_PROVIDER": "disabled"}
        with patch.object(supervisor, "load_private_settings", return_value=config):
            environment = supervisor.private_environment(source_root.parent / ".local" / "synthetic-test.json")
        environment["ACADEMIC_PUBLIC_DEMO"] = "1"
        with tempfile.TemporaryDirectory(prefix="base-runtime-evidence-test-") as temporary:
            data = pdf_bytes([[('Approved graduation requirement', 50, 650)]])
            source = Path(temporary) / "approved.pdf"
            source.write_bytes(data)
            registry = registry_for(data)
            request = json.dumps({"source_path": str(source), "rule": registry.rules[RULE],
                                  "source": registry.sources[SOURCE]}).encode()
            workload = (
                "import importlib.metadata,json,os,sys\nfrom io import BytesIO\nfrom types import SimpleNamespace\n"
                "assert sys.prefix==sys.base_prefix\n"
                "assert importlib.metadata.version('pydantic')=='2.11.9'\n"
                "assert importlib.metadata.version('pypdfium2')=='5.10.1'\n"
                "import pypdfium2 as pdfium\n"
                "from academic_assistant.transcript_process import extract_isolated,_transcript_child_environment,_preview_child_environment\n"
                "from academic_assistant.evidence_pdf import EvidencePdfService\n"
                "from academic_assistant.evidence_process import preview_isolated\n"
                "request=json.loads(sys.stdin.buffer.read())\n"
                "assert os.environ['NEO4J_PASSWORD']=='synthetic-secret'\n"
                "assert 'NEO4J_PASSWORD' not in _preview_child_environment()\n"
                "assert 'NEO4J_PASSWORD' not in _transcript_child_environment()\n"
                "document=pdfium.PdfDocument.new(); document.new_page(100,100).close()\n"
                "stream=BytesIO(); document.save(stream); document.close()\n"
                "transcript=extract_isolated(stream.getvalue())\n"
                "registry=SimpleNamespace(rules={request['rule']['rule_id']:request['rule']},"
                "sources={request['source']['source_id']:request['source']})\n"
                "service=EvidencePdfService(registry,{request['source']['source_id']:request['source_path']})\n"
                "metadata,_=preview_isolated(service,request['rule']['rule_id'])\n"
                "print(json.dumps({'transcript':transcript.extraction_method,'precision':metadata.precision}),flush=True)\n"
            )
            output = transcript_process._run_bounded_child([sys._base_executable, "-c", workload], request,
                environment=environment, timeout_seconds=10, output_limit=1024)
            self.assertEqual({"transcript": "manual_required", "precision": "exact"}, json.loads(output))
            self.assertEqual(data, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
