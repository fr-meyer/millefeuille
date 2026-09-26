"""One-shot publication socket checks with no live paper writes."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain import paper_card_output_replay_reservation as reservation
from millefeuille.domain import paper_card_publication_socket as publication_socket
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.paper_card_publication_bundle import (
    plan_gpt_card_publication_bundle,
)
from millefeuille.domain.paper_card_publication_fs import GptCardFilesystemCommit
from tests import test_millefeuille_paper_card_output_write_scope as scope_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGptCardPublicationSocket(unittest.TestCase):
    def setUp(self):
        fixture = scope_tests.TestGptCardOutputWriteScope(
            "test_exact_bundle_and_preview_have_no_effects"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.control = Path(temporary.name) / "control"
        self.control.mkdir(mode=0o755)
        self.socket_path = self.control / "gpt-card-publication.sock"
        self.values = fixture.values

    def _patch_control(self):
        return (
            patch.object(publication_socket, "_CONTROL_DIR", self.control),
            patch.object(publication_socket, "_SOCKET_PATH", self.socket_path),
            patch.object(publication_socket, "_SOCKET_OWNER_UID", os.getuid()),
            patch.object(reservation, "_CONTROL_DIR", self.control),
            patch.object(reservation, "_CONTROL_OWNER_UID", os.getuid()),
            patch.object(publication_socket.os, "geteuid", return_value=0),
            patch.object(publication_socket.os, "chown", return_value=None),
            patch(
                "pwd.getpwnam",
                return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()),
            ),
        )

    def test_bounded_one_shot_roundtrip_returns_only_commit_identity(self):
        bundle = plan_gpt_card_publication_bundle(**self.values)
        commit = GptCardFilesystemCommit(
            run_id=self.fixture.plan.run_id,
            file_count=len(bundle.files),
            total_bytes=bundle.total_bytes,
            bundle_manifest_sha256=bundle.bundle_manifest_sha256,
            source_pack_root=str(self.fixture.root),
        )
        errors: list[Exception] = []

        def serve():
            try:
                publication_socket.serve_one_publication(timeout_seconds=5)
            except Exception as exc:
                errors.append(exc)

        with ExitStack() as stack:
            for context in self._patch_control():
                stack.enter_context(context)
            publish = stack.enter_context(
                patch.object(
                    publication_socket,
                    "publish_trusted_gpt_card_handoff",
                    return_value=commit,
                )
            )
            server = threading.Thread(target=serve)
            server.start()
            for _ in range(500):
                if (
                    self.socket_path.exists()
                    and stat.S_IMODE(self.socket_path.stat().st_mode) == 0o660
                ):
                    break
                time.sleep(0.01)
            else:
                self.fail("publication socket never became ready")
            actual = publication_socket._require_peer_uid
            denied = []

            def peer_check(channel, expected_uid):
                if threading.current_thread() is server and not denied:
                    denied.append(True)
                    raise MillefeuilleContractError("injected unauthorized first peer")
                actual(channel, expected_uid)

            with patch.object(
                publication_socket, "_require_peer_uid", side_effect=peer_check
            ):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as untrusted:
                    untrusted.connect(str(self.socket_path))
                    untrusted.settimeout(2)
                    self.assertEqual(untrusted.recv(1), b"")
                ack = publication_socket.request_gpt_card_publication(**self.values)
            self.assertEqual(denied, [True])
            server.join(timeout=6)
            self.assertFalse(server.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(ack.bundle_manifest_sha256, bundle.bundle_manifest_sha256)
            self.assertEqual(ack.file_count, len(bundle.files))
            self.assertFalse(self.socket_path.exists())
            with self.assertRaises(MillefeuilleContractError):
                publication_socket.request_gpt_card_publication(**self.values)
        self.assertFalse(
            (
                self.fixture.root
                / "analyses"
                / "millefeuille"
                / self.fixture.plan.run_id
            ).exists()
        )

    def test_parser_rejects_noncanonical_or_wrong_approval_shape(self):
        with self.assertRaises((MillefeuilleContractError, ValueError)):
            publication_socket._parse_request(b"{}")
        with self.assertRaises((MillefeuilleContractError, ValueError)):
            publication_socket._parse_request(b'{"x":1,"x":2}')

    def test_wrong_peer_does_not_consume_authorized_request(self):
        bundle = plan_gpt_card_publication_bundle(**self.values)
        commit = GptCardFilesystemCommit(
            run_id=self.fixture.plan.run_id,
            file_count=len(bundle.files),
            total_bytes=bundle.total_bytes,
            bundle_manifest_sha256=bundle.bundle_manifest_sha256,
            source_pack_root=str(self.fixture.root),
        )
        errors: list[Exception] = []
        rejected = threading.Event()
        original_require_peer_uid = publication_socket._require_peer_uid

        def check_peer(channel, uid):
            if threading.current_thread() is server and not rejected.is_set():
                rejected.set()
                raise MillefeuilleContractError("wrong peer")
            return original_require_peer_uid(channel, uid)

        def serve():
            try:
                publication_socket.serve_one_publication(timeout_seconds=5)
            except Exception as exc:
                errors.append(exc)

        with ExitStack() as stack:
            for context in self._patch_control():
                stack.enter_context(context)
            publish = stack.enter_context(
                patch.object(
                    publication_socket,
                    "publish_trusted_gpt_card_handoff",
                    return_value=commit,
                )
            )
            stack.enter_context(
                patch.object(publication_socket, "_require_peer_uid", check_peer)
            )
            server = threading.Thread(target=serve)
            server.start()
            for _ in range(500):
                if (
                    self.socket_path.exists()
                    and stat.S_IMODE(self.socket_path.stat().st_mode) == 0o660
                ):
                    break
                time.sleep(0.01)
            else:
                self.fail("publication socket never became ready")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.connect(str(self.socket_path))
            self.assertTrue(rejected.wait(timeout=2))
            self.assertTrue(server.is_alive())
            ack = publication_socket.request_gpt_card_publication(**self.values)
            server.join(timeout=6)
            self.assertFalse(server.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(ack.bundle_manifest_sha256, bundle.bundle_manifest_sha256)
            self.assertFalse(self.socket_path.exists())
        self.assertFalse(
            (
                self.fixture.root
                / "analyses"
                / "millefeuille"
                / self.fixture.plan.run_id
            ).exists()
        )

    def test_oversize_metadata_is_denied_before_broker_call(self):
        errors: list[Exception] = []

        def serve():
            try:
                publication_socket.serve_one_publication(timeout_seconds=5)
            except Exception as exc:
                errors.append(exc)

        with ExitStack() as stack:
            for context in self._patch_control():
                stack.enter_context(context)
            publish = stack.enter_context(
                patch.object(publication_socket, "publish_trusted_gpt_card_handoff")
            )
            server = threading.Thread(target=serve)
            server.start()
            for _ in range(500):
                if (
                    self.socket_path.exists()
                    and stat.S_IMODE(self.socket_path.stat().st_mode) == 0o660
                ):
                    break
                time.sleep(0.01)
            else:
                self.fail("publication socket never became ready")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.connect(str(self.socket_path))
                channel.sendall(
                    struct.pack("!I", publication_socket._METADATA_LIMIT + 1)
                )
                size = struct.unpack("!I", publication_socket._read_exact(channel, 4))[
                    0
                ]
                reply = publication_socket._read_exact(channel, size)
            server.join(timeout=6)
            self.assertFalse(server.is_alive())
            self.assertEqual(errors, [])
            self.assertIn(b'"status":"denied"', reply)
            publish.assert_not_called()
            self.assertFalse(self.socket_path.exists())
