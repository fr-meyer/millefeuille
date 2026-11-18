"""Bounded actual spawned lifecycle tests; no HTTP/DNS/credentials."""

import ctypes
import os
import signal
import sys
import unittest
from unittest.mock import patch

from millefeuille.cli.live_operator import _controlled_termination
from millefeuille.domain import writeback_http as h
from millefeuille.domain import writeback_http_process as p
from tests import test_millefeuille_writeback_http_process as base

MANAGED = (
    (signal.SIGINT, signal.SIGTERM, signal.SIGHUP) if sys.platform == "linux" else ()
)
FAST = """
from millefeuille.domain import writeback_http as h
from millefeuille.domain.writeback_http_worker import _main
async def fast(*args,**kwargs):return h._Acknowledgement(10)
h._HTTPBoundary._exchange=fast
_main()
"""
SLEEP = "import time;time.sleep(30)"


class LifecycleTests(unittest.TestCase):
    setUp = base.ProcessHTTPTests.setUp
    set_up_linux_supervisor = base.ProcessHTTPTests.set_up_linux_supervisor
    assert_unsupported_platform = base.ProcessHTTPTests.assert_unsupported_platform
    worker = base.ProcessHTTPTests.worker
    invoke = base.ProcessHTTPTests.invoke
    assert_reaped = base.ProcessHTTPTests.assert_reaped

    def state(self):
        value = ctypes.c_int()
        self.assertEqual(ctypes.CDLL(None).prctl(37, ctypes.byref(value), 0, 0, 0), 0)
        return (
            [signal.getsignal(n) for n in MANAGED],
            signal.pthread_sigmask(signal.SIG_BLOCK, set()),
            value.value,
        )

    def assert_restored(self, before):
        self.assertEqual(self.state(), before)
        self.assertFalse(p._LOCK.locked())
        self.assert_reaped()

    def test_cdll_selector_failure_and_actual_next_call(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        for target in ("cdll", "selector"):
            before = self.state()
            context = (
                patch.object(
                    p.ctypes, "CDLL", side_effect=OSError("dummy setup failure")
                )
                if target == "cdll"
                else patch.object(
                    p.selectors,
                    "DefaultSelector",
                    side_effect=OSError("dummy setup failure"),
                )
            )
            with context, self.assertRaises(ValueError):
                self.invoke(FAST, method="PATCH")
            self.assert_restored(before)
            self.assertEqual(self.invoke(FAST, method="PATCH").version, 10)
            self.assert_restored(before)

    def test_real_sighup_during_exchange(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        with _controlled_termination():
            before = self.state()
            with self.assertRaises(ValueError):
                self.invoke(
                    "import os,signal,time;os.kill(os.getppid(),signal.SIGHUP);"
                    "time.sleep(30)"
                )
            self.assert_restored(before)

    def test_repeated_mixed_cleanup_and_restore_cancel_no_ack(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        sentinel = self.original_popen(
            [sys.executable, "-I", "-c", "import time;time.sleep(30)"],
            start_new_session=True,
        )

        def cleanup_sentinel():
            if sentinel.poll() is None:
                sentinel.kill()
            sentinel.wait(timeout=1)

        self.addCleanup(cleanup_sentinel)
        descendant = FAST.replace(
            "return h._Acknowledgement(10)",
            "__import__('subprocess').Popen([__import__('sys').executable,"
            " '-I', '-c', 'import time;time.sleep(30)'],"
            " stdin=-3, stdout=-3, stderr=-3); return h._Acknowledgement(10)",
        )
        with _controlled_termination():
            before = self.state()
            real_kill = p._kill_process_tree
            real_signal = signal.signal
            old = dict(zip(MANAGED, before[0], strict=True))
            kills = []

            def mixed():
                for number in MANAGED:
                    os.kill(os.getpid(), number)

            def interrupted(process):
                kills.append(process.pid)
                mixed()
                if len(kills) == 1:
                    raise KeyboardInterrupt()
                real_kill(process)

            def restore(number, handler):
                out = real_signal(number, handler)
                if number in old and handler == old[number]:
                    mixed()
                return out

            with (
                patch.object(p, "_kill_process_tree", side_effect=interrupted),
                patch.object(p.signal, "signal", side_effect=restore),
                self.assertRaises(h._AmbiguousWriteError),
            ):
                self.invoke(descendant, method="PATCH")
            self.assertEqual(len(kills), 2)
            self.assertEqual(len(self.processes), 1)
            self.assert_restored(before)
            self.assertIsNone(sentinel.poll())

    def test_partial_handler_installation_and_next_call(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        before = self.state()
        real_signal = signal.signal
        count = 0

        def install(number, handler):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("partial handler install")
            return real_signal(number, handler)

        with (
            patch.object(p.signal, "signal", side_effect=install),
            self.assertRaises(ValueError),
        ):
            self.invoke(FAST, method="PATCH")
        self.assert_restored(before)
        self.assertEqual(self.invoke(FAST, method="PATCH").version, 10)

    def test_prctl_setup_and_interrupted_restore(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        real_factory = ctypes.CDLL
        for mode in ("setup", "restore"):
            before = self.state()
            real = real_factory(None)
            set_calls = 0

            class Proxy:
                def prctl(self, operation, *args, mode=mode, real=real):
                    nonlocal set_calls
                    if operation == 36:
                        set_calls += 1
                        if mode == "setup" and set_calls == 1:
                            return -1
                        if mode == "restore" and set_calls == 2:
                            for number in MANAGED:
                                os.kill(os.getpid(), number)
                            raise KeyboardInterrupt()
                    return real.prctl(operation, *args)

            with (
                patch.object(p.ctypes, "CDLL", return_value=Proxy()),
                self.assertRaises(ValueError),
            ):
                self.invoke(FAST, method="PATCH")
            self.assert_restored(before)
            self.assertEqual(self.invoke(FAST, method="PATCH").version, 10)

    def test_refusal_preserved_under_cleanup_cancellation(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        before = self.state()
        real = p._kill_process_tree

        def kill(process):
            for number in MANAGED:
                os.kill(os.getpid(), number)
            real(process)

        source = FAST.replace(
            "return h._Acknowledgement(10)", "raise h._DefinitiveRefusalError('dummy')"
        )
        with (
            patch.object(p, "_kill_process_tree", side_effect=kill),
            self.assertRaises(h._DefinitiveRefusalError),
        ):
            self.invoke(source, method="PATCH")
        self.assert_restored(before)

    def test_selector_close_fault_denies_ack_and_next_call_works(self):
        if sys.platform != "linux":
            return self.assert_unsupported_platform()
        before = self.state()
        factory = p.selectors.DefaultSelector
        attempts = []

        def selector():
            obj = factory()
            real_close = obj.close

            def close():
                attempts.append(1)
                if len(attempts) == 1:
                    raise OSError("synthetic close fault")
                real_close()

            obj.close = close
            return obj

        with (
            patch.object(p.selectors, "DefaultSelector", side_effect=selector),
            self.assertRaises(h._AmbiguousWriteError),
        ):
            self.invoke(FAST, method="PATCH")
        self.assertEqual(len(attempts), 2)
        self.assert_restored(before)
        self.assertEqual(self.invoke(FAST, method="PATCH").version, 10)
