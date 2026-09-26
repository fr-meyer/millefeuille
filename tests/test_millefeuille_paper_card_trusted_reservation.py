"""Trusted card approval and durable one-use reservation in isolated controls."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import paper_card_replay_reservation as replay
from millefeuille.domain import paper_card_trusted_approval as trusted
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests import test_millefeuille_paper_card_execution_scope as scope_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestTrustedGptCardReservation(unittest.TestCase):
    def setUp(self):
        fixture = scope_tests.TestGptCardApprovalScope(
            "test_exact_one_call_scope_has_no_effects"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.packet, self.receipt = scope_tests._card_pair(fixture.root, fixture.plan)
        self.values = dict(fixture.values, packet=self.packet, receipt=self.receipt)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.control = Path(temporary.name)
        self.approval_path = self.control / "gpt-card-approval.json"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, value in (
            ("_TRUSTED_APPROVAL_PATH", self.approval_path),
            ("_TRUSTED_APPROVAL_OWNER_UID", os.getuid()),
        ):
            self.stack.enter_context(patch.object(trusted, target, value))
        self.stack.enter_context(patch.object(replay, "_CONTROL_DIR", self.control))
        self.stack.enter_context(
            patch.object(replay, "_CONTROL_OWNER_UID", os.getuid())
        )
        self.record = {
            "schema_version": "millefeuille-gpt-card-approval/v0.1",
            "receipt_id": self.receipt.receipt_id,
            "receipt_digest": self.receipt.content_digest,
            "packet_digest": self.packet.content_digest,
            "manifest_sha256": fixture.plan.manifest_sha256,
            "paper_id": fixture.plan.paper_id,
            "run_id": fixture.plan.run_id,
            "summary_publication_sha256": fixture.plan.publication_manifest_sha256,
            "request_count": 1,
            "approver_id": self.receipt.approval.approver_id,
            "approved_at": self.receipt.approval.approved_at,
        }

    def _write(self, record=None):
        self.approval_path.write_text(
            trusted._canonical_json(record or self.record) + "\n"
        )
        self.approval_path.chmod(0o600)

    def test_self_hashed_packet_is_not_administrator_approval(self):
        with self.assertRaises(MillefeuilleContractError):
            trusted.validate_trusted_gpt_card_approval(**self.values)
        self.assertFalse(list(self.control.iterdir()))

    def test_exact_trusted_record_has_no_effects_and_rejects_identity_drift(self):
        self._write()
        original = self.approval_path.read_bytes()
        preview = trusted.validate_trusted_gpt_card_approval(**self.values)
        self.assertEqual(preview.manifest_sha256, self.fixture.plan.manifest_sha256)
        self.assertEqual(self.approval_path.read_bytes(), original)
        self.assertEqual(list(self.control.iterdir()), [self.approval_path])
        for key, value in (
            ("manifest_sha256", "sha256:" + "0" * 64),
            ("summary_publication_sha256", "sha256:" + "0" * 64),
            ("run_id", "other-run"),
            ("request_count", True),
            ("packet_digest", "sha256:" + "0" * 64),
        ):
            self._write({**self.record, key: value})
            with self.subTest(key=key), self.assertRaises(MillefeuilleContractError):
                trusted.validate_trusted_gpt_card_approval(**self.values)

    def test_unsafe_or_symlink_trusted_record_is_rejected(self):
        self._write()
        self.approval_path.chmod(0o666)
        with self.assertRaises(MillefeuilleContractError):
            trusted.validate_trusted_gpt_card_approval(**self.values)
        self.approval_path.unlink()
        other = self.control / "other.json"
        other.write_text(trusted._canonical_json(self.record) + "\n")
        self.approval_path.symlink_to(other)
        with self.assertRaises(MillefeuilleContractError):
            trusted.validate_trusted_gpt_card_approval(**self.values)

    def test_nonroot_reservation_is_rejected_without_creating_ledger(self):
        self._write()
        with (
            patch.object(replay.os, "geteuid", return_value=1000),
            self.assertRaises(MillefeuilleContractError),
        ):
            replay.reserve_trusted_gpt_card_receipt(**self.values)
        self.assertFalse((self.control / "gpt-card.sqlite3").exists())

    def test_reservation_is_durable_single_use_and_contains_only_control_metadata(self):
        self._write()
        with (
            patch.object(replay.sys, "platform", "linux"),
            patch.object(replay.os, "geteuid", return_value=0),
        ):
            first = replay.reserve_trusted_gpt_card_receipt(**self.values)
            with self.assertRaises(MillefeuilleContractError):
                replay.reserve_trusted_gpt_card_receipt(**self.values)
        database = self.control / "gpt-card.sqlite3"
        self.assertEqual(database.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT receipt_id,receipt_digest,audit_json FROM approvals"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:2], (first.receipt_id, first.receipt_digest))
        self.assertEqual(json.loads(rows[0][2])["status"], "consumed")
        self.assertNotIn("Private", rows[0][2])

    def test_untrusted_record_and_unsafe_ledger_never_reserve(self):
        with (
            patch.object(replay.sys, "platform", "linux"),
            patch.object(replay.os, "geteuid", return_value=0),
            self.assertRaises(MillefeuilleContractError),
        ):
            replay.reserve_trusted_gpt_card_receipt(**self.values)
        self.assertFalse((self.control / "gpt-card.sqlite3").exists())
        self._write()
        database = self.control / "gpt-card.sqlite3"
        database.write_bytes(b"unsafe")
        database.chmod(0o666)
        with (
            patch.object(replay.sys, "platform", "linux"),
            patch.object(replay.os, "geteuid", return_value=0),
            self.assertRaises(MillefeuilleContractError),
        ):
            replay.reserve_trusted_gpt_card_receipt(**self.values)
        self.assertEqual(database.read_bytes(), b"unsafe")
