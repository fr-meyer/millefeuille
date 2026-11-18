"""Finite diagnostics controls; no external API or original-test changes."""

import json
import signal
import sys
import threading
import unittest
from unittest.mock import patch

from millefeuille.domain import _failure_observations as obs
from millefeuille.domain import writeback_http as http
from millefeuille.domain import writeback_http_process as process
from millefeuille.domain import writeback_live_broker as broker
from tests import test_millefeuille_writeback_http_process as base


class Observations(unittest.TestCase):
    def tearDown(self):
        obs.deactivate()

    def test_default_inert_generation_and_invalid_values(self):
        obs.deactivate()
        obs.record(None, boundary="supervisor", stage="pipes", reason="unconfirmed")
        self.assertIsNone(obs.drain())
        obs.activate()
        old = obs.token()
        for kwargs in (
            {"stage": "SECRET"},
            {"created": "SECRET"},
            {"reason": "SECRET"},
            {"cause": "SECRET"},
            {"method": "SECRET"},
            {"boundary": "SECRET"},
        ):
            values = {
                "boundary": "supervisor",
                "stage": "pipes",
                "reason": "unconfirmed",
            }
            values.update(kwargs)
            obs.record(old, **values)
        self.assertIsNone(obs.drain())
        obs.activate()
        obs.record(old, boundary="supervisor", stage="pipes", reason="unconfirmed")
        self.assertIsNone(obs.drain())

    def test_canaries_overflow_and_concurrent_threads(self):
        class SecretError(Exception):
            def __str__(self):
                raise AssertionError("SECRET rendered")

            def __repr__(self):
                raise AssertionError("SECRET represented")

        obs.activate()
        token = obs.token()

        def emit():
            for _ in range(100):
                obs.record(
                    token,
                    boundary="supervisor",
                    stage="pipes",
                    reason="unconfirmed",
                    exception=SecretError("SECRET"),
                )

        threads = [threading.Thread(target=emit) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        value = obs.drain()
        raw = json.dumps(value)
        self.assertLess(len(raw), 8192)
        self.assertNotIn("SECRET", raw)
        self.assertEqual(len(value["records"]), 8)
        self.assertGreater(value["dropped"], 0)
        self.assertEqual({r["category"] for r in value["records"]}, {"other"})
        self.assertIsNone(obs.drain())

    def test_reentrant_recording_remains_bounded(self):
        from collections import deque

        obs.activate()
        token = obs.token()

        class Reentrant(deque):
            entered = False

            def append(self, value):
                if not self.entered:
                    self.entered = True
                    obs.record(
                        token,
                        boundary="broker",
                        stage="request",
                        reason="broker-denial",
                    )
                super().append(value)

        token["records"] = Reentrant(maxlen=8)
        obs.record(token, boundary="broker", stage="request", reason="broker-denial")
        self.assertEqual(len(obs.drain()["records"]), 2)

    def test_recorder_fault_and_cancellation(self):
        obs.activate()

        class Broken:
            def __len__(self):
                raise OSError("SECRET")

        obs.token()["records"] = Broken()
        obs.record(
            obs.token(), boundary="broker", stage="request", reason="broker-denial"
        )

        class Cancelled:
            def __len__(self):
                raise KeyboardInterrupt()

        obs.token()["records"] = Cancelled()
        with self.assertRaises(KeyboardInterrupt):
            obs.record(
                obs.token(), boundary="broker", stage="request", reason="broker-denial"
            )

    def test_request_generation_survives_test_reset_without_leak(self):
        import os
        import socket
        import struct

        obs.activate()
        if not hasattr(socket, "SO_PEERCRED"):
            from unittest.mock import Mock
            channel = Mock()
            with patch.object(broker, "_process") as processing:
                with self.assertRaisesRegex(
                    broker.Error, "GPT card broker needs peer credentials"
                ):
                    broker.serve_connection(channel, {"caller_uid": 0})
                processing.assert_not_called()
            channel.recv.assert_not_called()
            channel.send.assert_not_called()
            self.assertIsNone(obs.drain())
            return

        def delayed(payload, config, state):
            obs.activate()  # next test starts while original request is in flight
            raise ValueError("SECRET")

        left, right = socket.socketpair()
        with left, right, patch.object(broker, "_process", side_effect=delayed):
            left.sendall(struct.pack("!I", 2) + b"{}")
            broker.serve_connection(right, {"caller_uid": os.getuid()})
            left.settimeout(1)
            output = left.recv(4096)
        self.assertIsNone(obs.drain())
        self.assertEqual(
            json.loads(output[4:]), {"status": "denied-or-unconfirmed", "retry": False}
        )
        self.assertNotIn(b"SECRET", output)

    def test_broker_known_pre_and_post_boundary_no_public_change(self):
        for stage, consumed in (
            ("evidence", False),
            ("execute", True),
            ("preparation", None),
        ):
            error = ValueError("SECRET")

            def failing(
                payload, config, state, stage=stage, consumed=consumed, error=error
            ):
                state[:] = [stage, consumed]
                raise error

            for enabled in (False, True):
                obs.activate() if enabled else obs.deactivate()
                with (
                    patch.object(broker, "_process", side_effect=failing),
                    self.assertRaises(ValueError) as caught,
                ):
                    broker.process({}, {})
                self.assertIs(caught.exception, error)
                value = obs.drain()
                if enabled:
                    self.assertEqual(value["records"][0]["consumption"], consumed)
                    self.assertNotIn("SECRET", json.dumps(value))
                else:
                    self.assertIsNone(value)


class ActualSupervisor(unittest.TestCase):
    def setUp(self):
        self.fixture = base.ProcessHTTPTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(obs.deactivate)
        obs.activate()

    def invoke(self, source, method="GET", seconds=1.5):
        with self.assertRaises(ValueError) as caught:
            self.fixture.invoke(source, method=method, seconds=seconds)
        self.fixture.assert_reaped()
        value = obs.drain()
        self.assertIsNotNone(value)
        self.assertNotIn("DUMMYSECRET", json.dumps(value))
        return caught.exception, value["records"][-1]

    def test_operational_deadline_actual_reaping(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        _, row = self.invoke("import time;time.sleep(30)")
        self.assertEqual(row["category"], "timeout")
        self.assertTrue(row["clean"])
        self.assertIsNone(row["expired"])  # primary short-circuits final clock

    def test_incomplete_actual_frame(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        _, row = self.invoke(
            "import sys;sys.stdout.buffer.write(b'bad');sys.stdout.flush()"
        )
        self.assertEqual(row["cause"], "fault-8")
        self.assertTrue(row["clean"])

    def test_nonzero_exit_after_complete_frame(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        source = (
            "import sys,struct;"
            'wire=b\'{"kind":"ok","result":{}}\';'
            "sys.stdout.buffer.write(struct.pack('!I',len(wire))+wire);"
            "sys.stdout.flush();"
            "raise SystemExit(7)"
        )
        _, row = self.invoke(source)
        self.assertEqual(row["cause"], "fault-10")
        self.assertTrue(row["frame"])
        self.assertTrue(row["clean"])

    def test_enabled_disabled_public_error_equivalence(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        outcomes = []
        for enabled in (False, True):
            obs.activate() if enabled else obs.deactivate()
            with self.assertRaises(ValueError) as caught:
                self.fixture.invoke("raise SystemExit(7)")
            self.fixture.assert_reaped()
            outcomes.append((type(caught.exception), str(caught.exception)))
            self.assertEqual(obs.drain() is not None, enabled)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_worker_secret_echo_is_never_serialized(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        source = (
            "import sys,struct;wire=b'DUMMYSECRET';"
            "sys.stdout.buffer.write(struct.pack('!I',len(wire))+wire);"
            "sys.stdout.flush()"
        )
        _, row = self.invoke(source)
        self.assertEqual(row["cause"], "fault-7")
        self.assertNotIn("DUMMYSECRET", json.dumps(row))
        self.assertTrue(row["clean"])

    def test_worker_unconfirmed(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        source = (
            "import sys,struct;"
            'wire=b\'{"kind":"unconfirmed"}\';'
            "sys.stdout.buffer.write(struct.pack('!I',len(wire))+wire);"
            "sys.stdout.flush()"
        )
        _, row = self.invoke(source)
        self.assertEqual(row["cause"], "fault-9")
        self.assertTrue(row["clean"])

    def test_actual_cancellation_latch(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        _, row = self.invoke(
            "import os,signal,time;os.kill(os.getppid(),signal.SIGTERM);time.sleep(30)"
        )
        self.assertTrue(row["cancelled"])
        self.assertTrue(row["clean"])

    def test_refusal_priority_with_late_exit(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        source = (
            "import sys,struct,time;"
            'wire=b\'{"kind":"refused"}\';'
            "sys.stdout.buffer.write(struct.pack('!I',len(wire))+wire);"
            "sys.stdout.flush();"
            "time.sleep(30)"
        )
        error, row = self.invoke(source, method="PATCH")
        self.assertIsInstance(error, http._DefinitiveRefusalError)
        self.assertEqual(row["reason"], "refused")
        self.assertTrue(row["clean"])

    def test_faulted_recorder_preserves_public_outcome(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        with (
            patch.object(obs, "record", side_effect=OSError("SECRET")),
            self.assertRaisesRegex(ValueError, "contained read unconfirmed"),
        ):
            self.fixture.invoke("raise SystemExit(7)")
        self.fixture.assert_reaped()

    def test_restoration_failure_keeps_fail_closed(self):
        if sys.platform != "linux":
            self.fixture.assert_unsupported_platform()
            self.assertIsNone(obs.drain())
            return
        original = signal.signal
        old = signal.getsignal(signal.SIGTERM)

        def restore(number, handler):
            if number == signal.SIGTERM and handler is old:
                raise OSError("SECRET")
            return original(number, handler)

        try:
            with patch.object(process.signal, "signal", side_effect=restore):
                _, row = self.invoke("raise SystemExit(7)")
            self.assertTrue(row["restoration"])
            self.assertFalse(row["clean"])
        finally:
            original(signal.SIGTERM, old)
