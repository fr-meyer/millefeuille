"""Card socket transport uses real isolated approval and ledger controls."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
import socket
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain import paper_card_reservation_broker as broker
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests import test_millefeuille_paper_card_trusted_reservation as trusted_tests
from tests import test_millefeuille_summary_reservation_broker as summary_broker_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGptCardReservationBroker(unittest.TestCase):
    def setUp(self):
        fixture = trusted_tests.TestTrustedGptCardReservation(
            "test_reservation_is_durable_single_use_and_contains_only_control_metadata"
        )
        fixture.setUp()
        fixture._write()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.values = fixture.values
        self.control = fixture.control
        self.path = self.control / "card.sock"
        stack = ExitStack()
        self.addCleanup(stack.close)
        model_user = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        stack.enter_context(
            patch.dict(
                sys.modules, {"pwd": SimpleNamespace(getpwnam=lambda _name: model_user)}
            )
        )
        for key, value in (
            ("_CONTROL_DIR", self.control),
            ("_BROKER_SOCKET_PATH", self.path),
            ("_BROKER_OWNER_UID", os.getuid()),
        ):
            stack.enter_context(patch.object(broker, key, value))
        stack.enter_context(patch.object(broker.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(broker.os, "chown", return_value=None))

    def test_exact_reservation_over_socket_is_durable_and_replay_is_denied(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            for attempt in (1, 2):
                future = pool.submit(broker.serve_one_reservation, timeout_seconds=5)
                summary_broker_tests._wait_for_socket(self.path)
                if attempt == 1:
                    result = broker.request_gpt_card_reservation(**self.values)
                    self.assertEqual(result.request_count, 1)
                    self.assertEqual(result.provider_calls_performed, 0)
                else:
                    with self.assertRaises(MillefeuilleContractError):
                        broker.request_gpt_card_reservation(**self.values)
                future.result(timeout=5)
                self.assertFalse(self.path.exists())
        self.assertTrue((self.control / "gpt-card.sqlite3").is_file())

    def test_missing_publication_and_malformed_identity_are_denied_without_reservation(
        self,
    ):
        for payload in (
            {"packet": {}},
            {
                "packet": self.values["packet"].to_dict(),
                "receipt": self.values["receipt"].to_dict(),
                "evidence": {
                    key: str(self.values[key]) for key in broker._EVIDENCE_FIELDS
                },
                "run_id": self.values["run_id"],
                "publication": {"unexpected": "value"},
            },
        ):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(broker.serve_one_reservation, timeout_seconds=5)
                summary_broker_tests._wait_for_socket(self.path)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                    channel.settimeout(5)
                    channel.connect(str(self.path))
                    request = json.dumps(payload).encode()
                    channel.sendall(struct.pack("!I", len(request)) + request)
                    size = struct.unpack("!I", broker._read_exact(channel, 4))[0]
                    reply = json.loads(broker._read_exact(channel, size))
                future.result(timeout=5)
                self.assertEqual(reply["status"], "denied")
            self.assertFalse((self.control / "gpt-card.sqlite3").exists())

    def test_bad_peer_before_valid_node_does_not_end_listener(self):
        original = broker._require_peer_uid
        calls = 0

        def peer_check(channel, expected_uid):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise MillefeuilleContractError("unauthorized peer")
            return original(channel, expected_uid)

        with (
            patch.object(broker, "_require_peer_uid", side_effect=peer_check),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(broker.serve_one_reservation, timeout_seconds=5)
            summary_broker_tests._wait_for_socket(self.path)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bad:
                bad.connect(str(self.path))
                bad.settimeout(5)
                self.assertEqual(bad.recv(1), b"")
            result = broker.request_gpt_card_reservation(**self.values)
            future.result(timeout=5)
        self.assertEqual(result.request_count, 1)

    def test_wrong_or_corrupt_published_source_never_creates_socket_reservation(self):
        root_fixture = self.fixture.fixture.fixture.fixture
        path = root_fixture.root / root_fixture.plan.texts[0].ref
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(MillefeuilleContractError):
            broker.request_gpt_card_reservation(**self.values)
        self.assertFalse((self.control / "gpt-card.sqlite3").exists())
