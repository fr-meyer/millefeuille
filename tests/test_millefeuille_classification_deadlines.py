"""Actual local socket framing; dummy bytes and no privileged service."""

import os
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch

from millefeuille.domain import classification_live_broker as b


class ClassificationDeadlines(unittest.TestCase):
    def assert_peer_unavailable(self):
        from unittest.mock import Mock

        channel = Mock()
        with patch.object(b, "_process") as process:
            with self.assertRaisesRegex(
                b.Error, "GPT card broker needs peer credentials"
            ):
                b._serve_connection(channel, 0)
            process.assert_not_called()
        channel.recv.assert_not_called()
        channel.send.assert_not_called()
        channel.getsockopt.assert_not_called()

    def pair(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        return left, right

    def drip(self, channel, fragments):
        stop = threading.Event()

        def run():
            for delay, data in fragments:
                if stop.wait(delay):
                    return
                try:
                    channel.sendall(data)
                except OSError:
                    return

        thread = threading.Thread(target=run)
        thread.start()

        def finish():
            stop.set()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())

        self.addCleanup(finish)

    def test_slow_header_times_out_before_dispatch(self):
        if not hasattr(socket, "SO_PEERCRED"):
            self.assert_peer_unavailable()
            return
        left, right = self.pair()
        self.drip(right, [(0.03, bytes([x])) for x in (0, 0, 0, 2)])
        with (
            patch.object(b, "EXCHANGE_SECONDS", 0.07),
            patch.object(b, "_process") as process,
        ):
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                b._serve_connection(left, os.getuid())
            self.assertLess(time.monotonic() - start, 0.2)
            process.assert_not_called()

    def test_slow_body_uses_remaining_header_budget(self):
        if not hasattr(socket, "SO_PEERCRED"):
            self.assert_peer_unavailable()
            return
        left, right = self.pair()
        self.drip(right, [(0.05, struct.pack("!I", 2)), (0.05, b"{"), (0.05, b"}")])
        with (
            patch.object(b, "EXCHANGE_SECONDS", 0.08),
            patch.object(b, "_process") as process,
        ):
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                b._serve_connection(left, os.getuid())
            self.assertLess(time.monotonic() - start, 0.14)
            process.assert_not_called()

    def test_fast_frame_and_actual_peer_check(self):
        if not hasattr(socket, "SO_PEERCRED"):
            self.assert_peer_unavailable()
            return
        left, right = self.pair()
        right.sendall(struct.pack("!I", 2) + b"{}")
        with patch.object(b, "_process", return_value={"status": "dummy"}) as process:
            b._serve_connection(left, os.getuid())
            process.assert_called_once_with({})
        deadline = time.monotonic() + 1
        size = struct.unpack("!I", b._read_exact(right, 4, deadline))[0]
        self.assertEqual(
            b.decode(b._read_exact(right, size, deadline)), {"status": "dummy"}
        )

    def test_eof_and_oversize_never_dispatch(self):
        if not hasattr(socket, "SO_PEERCRED"):
            self.assert_peer_unavailable()
            return
        for frame in (b"\x00", struct.pack("!I", b.LIMIT + 1)):
            with self.subTest(frame=frame):
                left, right = self.pair()
                right.sendall(frame)
                right.shutdown(socket.SHUT_WR)
                with patch.object(b, "_process") as process:
                    b._serve_connection(left, os.getuid())
                    process.assert_not_called()
                size = struct.unpack("!I", b._read_exact(right, 4))[0]
                self.assertEqual(
                    b.decode(b._read_exact(right, size))["status"],
                    "denied-or-unconfirmed",
                )

    def test_processing_expiry_does_not_send_or_retry(self):
        if not hasattr(socket, "SO_PEERCRED"):
            self.assert_peer_unavailable()
            return
        left, right = self.pair()
        right.sendall(struct.pack("!I", 2) + b"{}")

        def processed(_payload):
            time.sleep(0.05)
            return {"status": "effect-may-have-occurred"}

        with (
            patch.object(b, "EXCHANGE_SECONDS", 0.02),
            patch.object(b, "_process", side_effect=processed) as process,
        ):
            with self.assertRaises(TimeoutError):
                b._serve_connection(left, os.getuid())
            process.assert_called_once()
        right.setblocking(False)
        with self.assertRaises(BlockingIOError):
            right.recv(1)

    def test_blocked_send_is_bounded(self):
        left, _right = self.pair()
        left.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        if os.name == "nt":
            left.setblocking(False)
            filled = 0
            until = time.monotonic() + 1
            while filled < 16 * 1024 * 1024 and time.monotonic() < until:
                try:
                    filled += left.send(b"x" * 65536)
                except BlockingIOError:
                    break
            else:
                self.fail("bounded socket fill did not establish backpressure")
            self.assertGreater(filled, 0)
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            b._send_frame(left, b"x" * (1024 * 1024), start + 0.05)
        self.assertLess(time.monotonic() - start, 0.2)

    def test_historical_reader_now_times_out_instead_of_returning_full_data(self):
        left, right = self.pair()
        left.settimeout(0.05)
        self.drip(right, [(0.02, b"x")] * 8)
        with self.assertRaises(TimeoutError):
            b._read_exact(left, 8)

    def test_client_slow_reply_shared_budget_and_cli_disposal(self):
        from contextlib import redirect_stdout
        import gc
        import io
        import json
        from pathlib import Path
        from types import SimpleNamespace
        import weakref

        from millefeuille.cli import classification_operator as cli
        from millefeuille.domain import classification_live as live
        from tests.test_millefeuille_classification_live import (
            TestClassificationLive,
            pair,
            portable_cli_admission,
        )

        fixture = TestClassificationLive()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from millefeuille.domain.classification_live_scope import identity

        outcome = live.ClassificationOutcome(
            fixture.receipt.content_digest, identity(fixture.plan), fixture.execution()
        )
        weak = weakref.ref(outcome)
        slot = [outcome]
        files = live.validated_files(inputs=fixture.inputs, outcome=outcome)
        packet, receipt = pair(
            fixture.inputs, fixture.plan, live.output_identity(files)
        )
        del outcome, files
        root = Path(fixture.inputs["source_pack_root"])
        ip = root / "deadline-inputs.json"
        ip.write_text(json.dumps(fixture.inputs))
        args = ["execute-publish", "--mode", "approved-live", "--inputs", str(ip)]
        for name, value in (("packet", fixture.packet), ("receipt", fixture.receipt)):
            path = root / (name + ".json")
            path.write_text(json.dumps(value.to_dict()))
            args += ["--" + name, str(path)]
        left, right = self.pair()

        # Real socketpair IO, with only connect/root namespace identity emulated.
        exchange_started = []

        class Channel:
            def __enter__(self):
                # request() starts its one shared deadline immediately after this.
                exchange_started.append(time.monotonic())
                return self

            def __exit__(self, *unused):
                left.close()

            def connect(self, path):
                time.sleep(0.025)

            def __getattr__(self, name):
                return getattr(left, name)

        namespace = SimpleNamespace(
            parent=SimpleNamespace(
                lstat=lambda: SimpleNamespace(st_mode=0o40755, st_uid=0)
            ),
            lstat=lambda: SimpleNamespace(st_mode=0o140660, st_uid=0),
        )
        done = threading.Event()
        received = threading.Event()
        frame_timeouts = []
        real_read_exact = b._read_exact

        def observed_read(channel, size, deadline=None):
            try:
                return real_read_exact(channel, size, deadline)
            except TimeoutError:
                if isinstance(channel, Channel):
                    frame_timeouts.append((size, deadline is not None))
                raise

        def peer():
            try:
                deadline = time.monotonic() + 1
                size = struct.unpack("!I", b._read_exact(right, 4, deadline))[0]
                b._read_exact(right, size, deadline)
                received.set()
                for delay, data in (
                    (0.025, struct.pack("!I", 2)),
                    (0.04, b"{"),
                    (0.04, b"}"),
                ):
                    if done.wait(delay):
                        return
                    right.sendall(data)
            except OSError:
                pass

        thread = threading.Thread(target=peer)
        thread.start()
        try:
            with (
                portable_cli_admission(self, args),
                patch.object(b, "_read_exact", side_effect=observed_read),
                patch.object(b, "EXCHANGE_SECONDS", 0.07),
                patch.object(b, "SOCKET", namespace),
                # Real socketpair transport, not native Windows AF_UNIX support.
                # Replace only the broker binding, never the global socket module.
                patch.object(
                    b,
                    "socket",
                    SimpleNamespace(
                        AF_UNIX=getattr(socket, "AF_UNIX", object()),
                        SOCK_STREAM=socket.SOCK_STREAM,
                        socket=lambda *args: Channel(),
                    ),
                ),
                patch.object(b, "_require_peer_uid"),
                patch.object(
                    cli,
                    "execute_classification",
                    side_effect=lambda **kwargs: slot.pop(),
                ) as execute,
                patch.object(
                    cli,
                    "_publication_approval",
                    return_value={"packet": packet, "receipt": receipt},
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                start = time.monotonic()
                self.assertEqual(cli.run_classification_operator_cli(args), 3)
                if os.name == "nt":
                    # Windows runs a controlled downstream unit, not a supported
                    # POSIX CLI. Keep every real file/receipt validation, but time
                    # the broker exchange and CLI unwind from the actual deadline
                    # boundary, not the preceding disk-dependent preparation.
                    self.assertEqual(len(exchange_started), 1)
                    self.assertLess(time.monotonic() - exchange_started[0], 0.25)
                else:
                    # Preserve the original Linux whole-CLI assertion verbatim.
                    self.assertLess(time.monotonic() - start, 0.25)
                execute.assert_called_once()
                self.assertTrue(received.is_set())
                self.assertEqual(len(frame_timeouts), 1)
                self.assertIn(frame_timeouts[0][0], (4, 2))
                self.assertTrue(frame_timeouts[0][1])
            self.assertEqual(
                json.loads(output.getvalue().splitlines()[-1])["publication"],
                "not-confirmed",
            )
            self.assertNotIn("rationale", output.getvalue())
            gc.collect()
            self.assertIsNone(weak())
        finally:
            done.set()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
