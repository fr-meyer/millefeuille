"""Actual signals/children, dummy-only snapshots; no auth or providers."""

from contextlib import redirect_stdout, suppress
import io
import json
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

_DRIVER = r"""
import json, os, signal, sys, tempfile, time
from pathlib import Path
from millefeuille.cli import live_operator as cli
from millefeuille.clients import openclaw_model_client as adapter
root, phase = Path(sys.argv[1]), sys.argv[2]
old_kill = adapter._kill_process_tree
def slow_kill(process):
    (root/'cleaning').write_text('cleaning')
    time.sleep(.3)
    old_kill(process)
adapter._kill_process_tree = slow_kill
def operation(args):
    if phase == 'model':
        with tempfile.TemporaryDirectory(prefix='dummy-auth-', dir=root) as directory:
            snapshot = Path(directory)/'DUMMY_ONLY.json'
            snapshot.write_text('DUMMY_NOT_A_CREDENTIAL')
            (root/'snapshot').write_text(directory)
            child_code = (
                "import os,sys,time;from pathlib import Path;"
                "print('DUMMY_NOT_A_CREDENTIAL',flush=True);"
                "print('DUMMY_NOT_A_CREDENTIAL',file=sys.stderr,flush=True);"
                "Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)"
            )
            try:
                adapter._bounded_run(
                    [sys.executable, '-B', '-c', child_code, str(root/'ready')],
                    timeout=30,
                )
            finally:
                pid = int((root/'ready').read_text())
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    (root/'reaped-before-disposal').write_text(str(snapshot.exists()))
    elif phase == 'waiting':
        (root/'ready').write_text('waiting')
        cli._publication_approval(120)
    else:
        # Synthetic ambiguous transport: an attempt has started, no ack received.
        (root/'attempts').write_text('1')
        (root/'ready').write_text('ambiguous')
        time.sleep(30)
    (root/'confirmed').write_text('not allowed after cancellation')
cli._operate = operation
sys.exit(cli.run_live_operator_cli(['summary','execute-publish','--mode','approved-live','--route-evidence','dummy','--structure-evidence','dummy','--preparation','dummy','--source-pack-root','dummy','--artifact-root','dummy','--run-id','dummy']))
"""


@unittest.skipUnless(os.name == "posix", "POSIX signal controller")
class TestOperatorSignals(unittest.TestCase):
    def wait_file(self, path, process, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            if process.poll() is not None:
                self.fail("synthetic operator exited before synchronization")
            time.sleep(0.01)
        self.fail("synthetic synchronization timeout")

    def run_signal(self, phase, signum, repeated=False):
        with tempfile.TemporaryDirectory(prefix="mf-signal-") as directory:
            root = Path(directory)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", _DRIVER, str(root), phase],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            child_pid = None
            try:
                self.wait_file(root / "ready", process)
                if phase == "model":
                    child_pid = int((root / "ready").read_text())
                start = time.monotonic()
                process.send_signal(signum)
                if repeated:
                    self.wait_file(root / "cleaning", process)
                    for extra in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                        process.send_signal(extra)
                        time.sleep(0.03)
                stdout, stderr = process.communicate(timeout=8)
                self.assertLess(time.monotonic() - start, 8)
                self.assertEqual(process.returncode, 3, stderr)
                result = json.loads(stdout)
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["publication"], "not-confirmed")
                self.assertEqual(result["retry"], "forbidden-without-fresh-review")
                self.assertNotIn("DUMMY_NOT_A_CREDENTIAL", stdout + stderr)
                self.assertEqual(stderr, "")
                self.assertFalse((root / "confirmed").exists())
                if phase == "model":
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)
                    self.assertEqual(
                        (root / "reaped-before-disposal").read_text(), "True"
                    )
                    self.assertFalse(Path((root / "snapshot").read_text()).exists())
                    self.assertFalse((root / "attempts").exists())
                elif phase == "ambiguous":
                    self.assertEqual((root / "attempts").read_text(), "1")
                else:
                    self.assertFalse((root / "attempts").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)
                if child_pid is not None:
                    with suppress(ProcessLookupError):
                        os.killpg(child_pid, signal.SIGKILL)

    def test_sigterm_model_reaps_before_snapshot_disposal(self):
        self.run_signal("model", signal.SIGTERM)

    def test_sighup_model_reaps_before_snapshot_disposal(self):
        self.run_signal("model", signal.SIGHUP)

    def test_sigint_model_reaps_before_snapshot_disposal(self):
        self.run_signal("model", signal.SIGINT)

    def test_repeated_signals_do_not_interrupt_cleanup(self):
        self.run_signal("model", signal.SIGTERM, repeated=True)

    def test_termination_waiting_for_publication_approval(self):
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signum=signum):
                self.run_signal("waiting", signum)

    def test_ambiguous_publication_never_retries_or_confirms(self):
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signum=signum):
                self.run_signal("ambiguous", signum)

    def test_handlers_restored_and_unrelated_baseexception_propagates(self):
        before = {
            s: signal.getsignal(s)
            for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
        }
        args = [
            "summary",
            "plan",
            "--route-evidence",
            "x",
            "--structure-evidence",
            "x",
            "--preparation",
            "x",
            "--source-pack-root",
            "x",
            "--artifact-root",
            "x",
            "--run-id",
            "x",
        ]
        for error in (None, KeyboardInterrupt(), SystemExit(7)):
            with (
                patch.object(cli, "_operate", side_effect=error),
                redirect_stdout(io.StringIO()),
            ):
                if isinstance(error, SystemExit):
                    with self.assertRaises(SystemExit) as raised:
                        cli.run_live_operator_cli(args)
                    self.assertEqual(raised.exception.code, 7)
                else:
                    self.assertEqual(cli.run_live_operator_cli(args), 3 if error else 0)
            self.assertEqual(before, {s: signal.getsignal(s) for s in before})
