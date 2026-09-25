"""Offline ledger checks for one-use GPT summary receipt consumption."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import summary_replay_reservation as reservation
from millefeuille.domain.millefeuille import MillefeuilleContractError
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_summary_trusted_approval import (
    _approval_path,
    _case,
    _write_record,
)


@contextmanager
def _trusted_control(control: Path, approval: Path):
    with (
        _approval_path(approval),
        patch.object(reservation, "_CONTROL_DIR", control),
        patch.object(reservation, "_CONTROL_OWNER_UID", os.getuid()),
        patch.object(reservation.os, "geteuid", return_value=0),
    ):
        yield


@requires_secure_nofollow_writes
class TestGptSummaryReplayReservation(unittest.TestCase):
    def test_consumes_exact_receipt_once_without_paper_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "paper"
            root.mkdir()
            evidence, packet, receipt, record = _case(root)
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            _write_record(approval, record)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with _trusted_control(control, approval):
                preview = reservation.reserve_trusted_gpt_summary_receipt(
                    **evidence, packet=packet, receipt=receipt
                )
                with self.assertRaises(MillefeuilleContractError):
                    reservation.reserve_trusted_gpt_summary_receipt(
                        **evidence, packet=packet, receipt=receipt
                    )
            after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(preview.provider_calls_performed, 0)
            database = control / "gpt-summary.sqlite3"
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(database) as connection:
                rows = connection.execute("SELECT audit_json FROM approvals").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0][0])["status"], "consumed")

    def test_missing_approval_does_not_create_ledger(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "paper"
            root.mkdir()
            evidence, packet, receipt, _record = _case(root)
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            with (
                _trusted_control(control, approval),
                self.assertRaises(MillefeuilleContractError),
            ):
                reservation.reserve_trusted_gpt_summary_receipt(
                    **evidence, packet=packet, receipt=receipt
                )
            self.assertFalse((control / "gpt-summary.sqlite3").exists())

    def test_unprivileged_or_writable_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "paper"
            root.mkdir()
            evidence, packet, receipt, record = _case(root)
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            _write_record(approval, record)
            with (
                _approval_path(approval),
                patch.object(reservation, "_CONTROL_DIR", control),
                patch.object(reservation, "_CONTROL_OWNER_UID", os.getuid()),
                patch.object(reservation.os, "geteuid", return_value=1234),
                self.assertRaises(MillefeuilleContractError),
            ):
                reservation.reserve_trusted_gpt_summary_receipt(
                    **evidence, packet=packet, receipt=receipt
                )
            database = control / "gpt-summary.sqlite3"
            database.write_bytes(b"not-a-database")
            database.chmod(0o666)
            with (
                _trusted_control(control, approval),
                self.assertRaises(MillefeuilleContractError),
            ):
                reservation.reserve_trusted_gpt_summary_receipt(
                    **evidence, packet=packet, receipt=receipt
                )
