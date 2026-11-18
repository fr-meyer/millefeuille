"""Actual spawned processes; never DNS/network. Synthetic worker uses real IPC."""

import contextlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from millefeuille.domain import writeback_http as h
from millefeuille.domain import writeback_http_process as p


class ProcessHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mfwp-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = {
            "library_type": "user",
            "library_id": "12345",
            "key": "ABCD1234",
        }
        self.processes = []
        self.original_popen = subprocess.Popen
        if sys.platform == "linux":
            self.set_up_linux_supervisor()

    def set_up_linux_supervisor(self):
        self.original_alarm = signal.signal(
            signal.SIGALRM,
            lambda *_: (_ for _ in ()).throw(TimeoutError("outer test bound")),
        )
        signal.alarm(8)

        def cleanup():
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self.original_alarm)
            for process in self.processes:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=1)

        self.addCleanup(cleanup)

    def assert_unsupported_platform(self):
        """Real supervisor refusal, not Linux lifecycle qualification."""
        self.assertNotEqual(sys.platform, "linux")
        before = list(self.root.iterdir())
        with contextlib.ExitStack() as stack:
            # Fail before any worker/IPC, credential serialization or transport.
            guards = [
                stack.enter_context(
                    patch.object(owner, name, side_effect=AssertionError(name))
                )
                for owner, name in (
                    (p, "_worker_command"),
                    (p.subprocess, "Popen"),
                    (p, "_wire"),
                    (p.ctypes, "CDLL"),
                    (p.selectors, "DefaultSelector"),
                    (p.signal, "getsignal"),
                    (h._HTTPBoundary, "_exchange"),
                )
            ]
            for method in ("GET", "GET-COLLECTION", "PATCH"):
                with self.subTest(contract="non-Linux refusal", method=method):
                    with self.assertRaisesRegex(
                        h.Error,
                        "^contained HTTP requires Linux main-thread supervisor$",
                    ) as caught:
                        p._contained_exchange(
                            "DUMMYSECRET",
                            method,
                            self.identity,
                            time.monotonic() + 1.2,
                        )
                    self.assertIs(type(caught.exception), h.Error)
                    self.assertNotIn("DUMMYSECRET", str(caught.exception))
            for guard in guards:
                guard.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), before)
        self.assertEqual(self.processes, [])
        self.assertFalse(p._LOCK.locked())

    def worker(self, source):
        path = self.root / "worker.py"
        root = Path(h.__file__).resolve().parents[2]
        path.write_text(
            "import sys\nsys.path.insert(0," + repr(str(root)) + ")\n" + source
        )
        return [sys.executable, "-I", "-B", str(path)]

    def invoke(self, source, seconds=1.2, method="GET"):
        command = self.worker(source)

        def spawn(*args, **kwargs):
            self.assertNotIn("DUMMYSECRET", repr(args) + repr(kwargs))
            process = self.original_popen(*args, **kwargs)
            self.processes.append(process)
            return process

        with (
            patch.object(p, "_worker_command", return_value=command),
            patch.object(p.subprocess, "Popen", side_effect=spawn),
        ):
            if method == "GET":
                return h._HTTPReader("DUMMYSECRET").read(
                    self.identity, deadline=time.monotonic() + seconds
                )
            return h._HTTPWriter("DUMMYSECRET").patch(
                self.identity,
                expected_version=9,
                delta={"tags": [], "collections": []},
                deadline=time.monotonic() + seconds,
            )

    def assert_reaped(self):
        for process in self.processes:
            self.assertIsNotNone(process.poll())
            self.assertFalse(p._alive(process.pid))
            self.assertFalse(Path("/proc", str(process.pid)).exists())

    def test_indefinite_default_executor_and_descendant_are_reaped(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        sentinel = self.original_popen(
            [sys.executable, "-I", "-c", "import time;time.sleep(5)"],
            start_new_session=True,
        )

        def cleanup_sentinel():
            if sentinel.poll() is None:
                sentinel.kill()
            sentinel.wait(timeout=1)

        self.addCleanup(cleanup_sentinel)
        source = """
import asyncio,threading,subprocess,os
from millefeuille.domain import writeback_http as h
from millefeuille.domain.writeback_http_worker import _main
async def blocked(*args,**kwargs):
    subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)'])
    await asyncio.get_running_loop().run_in_executor(None,threading.Event().wait)
h._HTTPBoundary._exchange=blocked
_main()
"""
        start = time.monotonic()
        with self.assertRaises(ValueError):
            self.invoke(source)
        self.assertLess(time.monotonic() - start, 1.6)
        self.assert_reaped()
        self.assertIsNone(sentinel.poll())
        sentinel.kill()
        sentinel.wait(timeout=1)

    def test_delayed_executor_shutdown_cannot_publish_success(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        source = """
import asyncio,threading
from millefeuille.domain import writeback_http as h
from millefeuille.domain.writeback_http_worker import _main
async def delayed(*args,**kwargs):
    asyncio.get_running_loop().run_in_executor(None,threading.Event().wait)
    return h._Acknowledgement(10)
h._HTTPBoundary._exchange=delayed
_main()
"""
        with self.assertRaises(h._AmbiguousWriteError):
            self.invoke(source, method="PATCH")
        self.assertEqual(len(self.processes), 1)
        self.assert_reaped()

    def test_fast_actual_worker_ack_and_proof(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        source = """
import json
from millefeuille.domain import writeback_http as h
from millefeuille.domain.writeback_http_worker import _main
async def fast(self,method,identity,deadline,**kwargs):
    if method=='PATCH':return h._Acknowledgement(10)
    raw=json.dumps({'key':identity['key'],'version':9,'library':{'type':'user','id':12345},'data':{'key':identity['key'],'version':9,'dateModified':'2026-11-16T00:00:00Z','itemType':'journalArticle','tags':[],'collections':[]}}).encode()
    snapshot,proof=h._project(identity,raw,9)
    return {'snapshot':snapshot,'proof':proof}
h._HTTPBoundary._exchange=fast
_main()
"""
        out = self.invoke(source)
        h._checked_observation(out)
        self.assertNotIn("DUMMYSECRET", repr(out))
        ack = self.invoke(source, method="PATCH")
        self.assertEqual(ack.version, 10)
        self.assert_reaped()

    def test_malformed_oversize_exit_and_parent_signal(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        for source in (
            "import sys;sys.stdout.buffer.write(b'bad');sys.stdout.flush()",
            "import sys,struct;sys.stdout.buffer.write(struct.pack('!I',200001));"
            "sys.stdout.flush()",
            "raise SystemExit(7)",
            "import os,signal,time;os.kill(os.getppid(),signal.SIGTERM);time.sleep(30)",
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.invoke(source)
            self.assert_reaped()

    def test_explicit_refusal_retained(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        source = """
from millefeuille.domain import writeback_http as h
from millefeuille.domain.writeback_http_worker import _main
async def refuse(*args,**kwargs):raise h._DefinitiveRefusalError('private detail')
h._HTTPBoundary._exchange=refuse
_main()
"""
        with self.assertRaises(h._DefinitiveRefusalError) as caught:
            self.invoke(source, method="PATCH")
        self.assertNotIn("private detail", str(caught.exception))
        self.assert_reaped()

    def test_complete_refusal_with_delayed_exit_is_not_overwritten(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        source = """
import struct,time
wire=b'{"kind":"refused"}'
sys.stdout.buffer.write(struct.pack('!I',len(wire))+wire);sys.stdout.flush()
time.sleep(30)
"""
        with self.assertRaises(h._DefinitiveRefusalError):
            self.invoke(source, method="PATCH")
        self.assert_reaped()
