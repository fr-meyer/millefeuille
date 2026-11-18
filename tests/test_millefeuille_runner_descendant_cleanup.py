"""Dummy descendants exercise first cancellation during post-exit cleanup."""

from contextlib import nullcontext, suppress
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from millefeuille.cli import live_operator as cli
from millefeuille.clients import openclaw_model_client as adapter


@unittest.skipUnless(os.name == "posix", "POSIX process group cancellation")
class TestDescendantCleanup(unittest.TestCase):
    def descendant_case(self, signum=None, repeated=False):
        with tempfile.TemporaryDirectory(prefix="mf-desc-") as directory:
            root = Path(directory)
            marker, pidfile = root / "survived", root / "pid"
            delay = 1 if signum else 5
            descendant = (
                "import time,pathlib,sys;time.sleep(float(sys.argv[2]));"
                "pathlib.Path(sys.argv[1]).write_text('DUMMY');time.sleep(10)"
            )
            parent = (
                "import subprocess,sys,pathlib;"
                "p=subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],"
                "sys.argv[3],sys.argv[4]]);"
                "pathlib.Path(sys.argv[2]).write_text(str(p.pid))"
            )
            real_join = adapter.threading.Thread.join
            real_kill = adapter._kill_process_tree
            workers = []
            interrupted = False

            def join(worker, *args, **kwargs):
                nonlocal interrupted
                workers.append(worker)
                self.assertEqual(kwargs.get("timeout"), 1)
                if signum and not interrupted:
                    interrupted = True
                    os.kill(os.getpid(), signum)
                return real_join(worker, *args, **kwargs)

            def kill(process):
                # The direct child is already reaped; the group is not empty.
                self.assertIsNotNone(process.poll())
                if repeated:
                    for extra in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                        os.kill(os.getpid(), extra)
                real_kill(process)

            started = time.monotonic()
            try:
                expected = (
                    self.assertRaises(cli._OperatorCancelled)
                    if signum
                    else nullcontext()
                )
                with (
                    expected,
                    cli._controlled_termination(),
                    patch.object(adapter.threading.Thread, "join", join),
                    patch.object(
                        adapter, "_kill_process_tree", side_effect=kill
                    ) as killed,
                ):
                    result = adapter._bounded_run(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            parent,
                            descendant,
                            str(pidfile),
                            str(marker),
                            str(delay),
                        ],
                        input="DUMMY" * 100000,
                        timeout=5,
                    )
                    self.assertEqual(result.returncode, 0)
                self.assertGreaterEqual(killed.call_count, 1)
                self.assertTrue(workers)
                self.assertTrue(all(not worker.is_alive() for worker in workers))
                self.assertLess(time.monotonic() - started, 7)
                time.sleep(max(0, delay + 0.3 - (time.monotonic() - started)))
                self.assertFalse(marker.exists(), "descendant survived cleanup")
            finally:
                if pidfile.exists():
                    with suppress(ProcessLookupError):
                        os.kill(int(pidfile.read_text()), signal.SIGKILL)

    def test_first_sigterm_during_post_exit_join(self):
        self.descendant_case(signal.SIGTERM)

    def test_first_sighup_during_post_exit_join(self):
        self.descendant_case(signal.SIGHUP)

    def test_first_sigint_during_post_exit_join(self):
        self.descendant_case(signal.SIGINT)

    def test_repeated_signals_during_post_exit_unwind(self):
        self.descendant_case(signal.SIGTERM, repeated=True)

    def test_no_signal_cleans_descendant_and_blocked_writer(self):
        self.descendant_case()

    def test_primary_exception_survives_secondary_cleanup_failures(self):
        primary = SystemExit(17)
        real_wait = adapter.subprocess.Popen.wait
        real_kill = adapter._kill_process_tree
        first = True

        def wait(process, *args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise primary
            return real_wait(process, *args, **kwargs)

        def kill(process):
            real_kill(process)
            raise RuntimeError("dummy secondary cleanup failure")

        with (
            patch.object(adapter.subprocess.Popen, "wait", wait),
            patch.object(adapter, "_kill_process_tree", side_effect=kill),
            self.assertRaises(SystemExit) as raised,
        ):
            adapter._bounded_run(
                [sys.executable, "-B", "-c", "import time;time.sleep(10)"], timeout=5
            )
        self.assertIs(raised.exception, primary)

    def test_first_signal_during_timeout_cleanup_preserves_timeout(self):
        real_kill = adapter._kill_process_tree
        attempts, children = 0, []

        def kill(process):
            nonlocal attempts
            attempts += 1
            children.append(process)
            if attempts == 1:
                os.kill(os.getpid(), signal.SIGTERM)
            real_kill(process)

        with (
            cli._controlled_termination(),
            patch.object(adapter, "_kill_process_tree", side_effect=kill),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            adapter._bounded_run(
                [sys.executable, "-B", "-c", "import time;time.sleep(10)"], timeout=0.05
            )
        self.assertEqual(attempts, 2)
        self.assertTrue(all(process.poll() is not None for process in children))

    def test_partial_reader_start_failure_has_safe_bounded_cleanup(self):
        real_start = adapter.threading.Thread.start
        calls, started = 0, []
        primary = RuntimeError("dummy second reader start failure")

        def start(worker):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise primary
            started.append(worker)
            real_start(worker)

        with (
            patch.object(adapter.threading.Thread, "start", start),
            self.assertRaises(RuntimeError) as raised,
        ):
            adapter._bounded_run(
                [sys.executable, "-B", "-c", "import time;time.sleep(10)"], timeout=5
            )
        self.assertIs(raised.exception, primary)
        self.assertTrue(all(not worker.is_alive() for worker in started))
