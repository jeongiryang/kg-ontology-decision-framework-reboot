from __future__ import annotations

from io import BytesIO
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant import transcript_process as process_module
from academic_assistant.transcript_models import TranscriptExtraction


class TranscriptProcessTests(unittest.TestCase):
    def setUp(self):
        # Fake-Popen tests must not assign fake OS handles to a native job.
        self.job_patch=patch.object(process_module,"_WindowsJob")
        self.job_class=self.job_patch.start()
        self.addCleanup(self.job_patch.stop)

    def child(self, output=None, returncode=0):
        process=MagicMock()
        process.returncode=returncode
        process.poll.return_value=returncode
        if output is None:
            output=TranscriptExtraction(courses=[],issues=["합성 확인 필요"],extraction_method="manual_required").model_dump_json().encode("utf-8")
        process.communicate.return_value=(output,None)
        return process

    def test_pdf_is_stdin_only_and_whole_child_has_120_second_deadline(self):
        data=b"%PDF-synthetic-private-marker"
        process=self.child()
        with patch.object(process_module.subprocess,"Popen",return_value=process) as start:
            result=process_module.extract_isolated(data,page_number=6)
        self.assertEqual("manual_required",result.extraction_method)
        arguments=start.call_args.args[0]
        self.assertEqual(sys.executable,arguments[0])
        self.assertEqual(["-m","academic_assistant.transcript_process","6"],arguments[1:])
        self.assertNotIn("private-marker"," ".join(arguments))
        self.assertEqual(subprocess.DEVNULL,start.call_args.kwargs["stderr"])
        self.assertEqual(subprocess.PIPE,start.call_args.kwargs["stdin"])
        self.assertEqual(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,start.call_args.kwargs["creationflags"])
        self.assertEqual(os.name != "nt",start.call_args.kwargs["start_new_session"])
        if os.name == "nt":
            self.job_class.return_value.assign.assert_called_once_with(process)
            self.assertTrue(self.job_class.return_value.close.called)
        process.communicate.assert_called_once_with(input=data,timeout=120)
        self.assertTrue(process.stdin.close.called)
        self.assertTrue(process.stdout.close.called)

    def test_timeout_kills_and_waits_for_child_without_private_exception_text(self):
        process=self.child()
        process.poll.return_value=None
        process.communicate.side_effect=subprocess.TimeoutExpired("private marker",120,output=b"private output",stderr=b"private error")
        with patch.object(process_module.subprocess,"Popen",return_value=process):
            with self.assertRaises(ValueError) as caught:
                process_module.extract_isolated(b"%PDF-synthetic")
        self.assertEqual(process_module.ERROR_MESSAGE,str(caught.exception))
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=process_module.CLEANUP_TIMEOUT_SECONDS)

    def test_crash_invalid_json_schema_and_oversized_output_are_generic(self):
        for output,code in ((b"private crash",-11),(b"private nonjson",0),(b'{"private":"value"}',0),(b"x"*(process_module.MAX_JSON_BYTES+1),0)):
            with self.subTest(code=code,length=len(output)):
                with patch.object(process_module.subprocess,"Popen",return_value=self.child(output,code)):
                    with self.assertRaises(ValueError) as caught:
                        process_module.extract_isolated(b"%PDF-synthetic")
                self.assertEqual(process_module.ERROR_MESSAGE,str(caught.exception))

    def test_os_failure_is_sanitized(self):
        with patch.object(process_module.subprocess,"Popen",side_effect=OSError("private path")):
            with self.assertRaises(ValueError) as caught:
                process_module.extract_isolated(b"%PDF-synthetic")
        self.assertEqual(process_module.ERROR_MESSAGE,str(caught.exception))

    def test_missing_oversized_and_invalid_selection_do_not_start_child(self):
        with patch.object(process_module.subprocess,"Popen") as start:
            for data in (b"",None,b"x"*(process_module.MAX_PDF_BYTES+1)):
                with self.assertRaises(ValueError): process_module.extract_isolated(data)
            for page in (True,0,11,"6"):
                with self.assertRaises(ValueError): process_module.extract_isolated(b"%PDF-synthetic",page_number=page)
        start.assert_not_called()

    def test_actual_harmless_synthetic_pdf_child(self):
        self.job_patch.stop()
        document=pdfium.PdfDocument.new()
        try:
            document.new_page(100,100).close()
            stream=BytesIO(); document.save(stream)
            data=stream.getvalue()
        finally:
            document.close()
        result=process_module.extract_isolated(data)
        self.assertEqual([],result.courses)
        self.assertEqual("manual_required",result.extraction_method)
        self.assertTrue(result.requires_confirmation)

    def actual_owned_tree_failure(self, mode):
        """Use real sleepers and native cleanup; only replace the child workload."""
        self.job_patch.stop()
        real_start=subprocess.Popen
        hidden={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        sentinel=real_start([sys.executable,"-c","import time; time.sleep(5)"],
                            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**hidden)
        record={}
        temporary=tempfile.TemporaryDirectory(prefix="transcript-process-test-")
        pid_file=Path(temporary.name)/"owned-descendant.pid"
        kernel=None
        if os.name == "nt":
            from ctypes import wintypes
            kernel=ctypes.WinDLL("kernel32",use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            kernel.WaitForSingleObject.restype=wintypes.DWORD
            kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
            kernel.TerminateProcess.restype=wintypes.BOOL
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            kernel.CloseHandle.restype=wintypes.BOOL
        workload=(
            "import os,subprocess,sys,time\nfrom pathlib import Path\n"
            "sys.stdin.buffer.read()\n"
            "flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(3)'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)\n"
            f"Path({str(pid_file)!r}).write_text(str(child.pid),encoding='ascii')\n"
            + ("time.sleep(3)\n" if mode == "timeout" else "print('invalid synthetic JSON',flush=True)\n")
        )

        def observe():
            # Windows communicate does not expose partial stdout on timeout.
            # This temporary file contains only our harmless descendant PID.
            record["pid"]=int(pid_file.read_text(encoding="ascii"))
            if kernel is not None:
                # Keep a handle to this exact owned grandchild, immune to PID reuse.
                record["handle"]=kernel.OpenProcess(0x00100001,False,record["pid"])
                self.assertTrue(record["handle"])

        def launch(_arguments, **kwargs):
            process=real_start([sys.executable,"-c",workload],**kwargs)
            record["parent"]=process
            communicate=process.communicate
            def communicate_and_observe(*args,**options):
                try:
                    result=communicate(*args,**options)
                except subprocess.TimeoutExpired as error:
                    observe()
                    raise
                observe()
                return result
            process.communicate=communicate_and_observe
            return process

        started=time.monotonic()
        try:
            with patch.object(process_module.subprocess,"Popen",side_effect=launch), patch.object(process_module,"EXTRACTION_TIMEOUT_SECONDS",0.6):
                with self.assertRaises(ValueError) as caught:
                    process_module.extract_isolated(b"%PDF-harmless-synthetic")
            self.assertEqual(process_module.ERROR_MESSAGE,str(caught.exception))
            self.assertLess(time.monotonic()-started,2.0)
            self.assertIsNotNone(record["parent"].poll())
            if kernel is not None:
                self.assertEqual(0,kernel.WaitForSingleObject(record["handle"],1000),"owned grandchild survived")
            else:
                try:
                    os.kill(record["pid"],0)
                except ProcessLookupError:
                    pass
                else:
                    # Linux may briefly retain a killed grandchild as a zombie.
                    status=subprocess.run(["ps","-o","stat=","-p",str(record["pid"])],
                                          capture_output=True,text=True,timeout=1,check=False).stdout.strip()
                    self.assertTrue(not status or status.startswith("Z"),"owned grandchild survived")
            self.assertIsNone(sentinel.poll(),"unrelated process was terminated")
        finally:
            if kernel is not None and record.get("handle"):
                if kernel.WaitForSingleObject(record["handle"],0) == 258:
                    kernel.TerminateProcess(record["handle"],1)
                    kernel.WaitForSingleObject(record["handle"],1000)
                kernel.CloseHandle(record["handle"])
            elif record.get("pid"):
                try:
                    os.kill(record["pid"],signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if record.get("parent") and record["parent"].poll() is None:
                record["parent"].kill(); record["parent"].wait(timeout=3)
            if sentinel.poll() is None:
                sentinel.kill()
            sentinel.wait(timeout=3)
            temporary.cleanup()

    def test_actual_timeout_kills_owned_grandchild_and_preserves_unrelated_process(self):
        self.actual_owned_tree_failure("timeout")

    def test_actual_invalid_output_kills_descendant_even_after_parent_exits(self):
        self.actual_owned_tree_failure("invalid_json")


if __name__ == "__main__":
    unittest.main()
