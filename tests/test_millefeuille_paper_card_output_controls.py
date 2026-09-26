"""Trusted card write approval and one-use audit use isolated controls."""

from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import paper_card_output_replay_reservation as reservation
from millefeuille.domain import paper_card_output_trusted_approval as trusted
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.paper_card_publication_bundle import (
    plan_gpt_card_publication_bundle,
)
from tests import test_millefeuille_paper_card_output_write_scope as scope_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


def _case(owner):
    fixture = scope_tests.TestGptCardOutputWriteScope(
        "test_exact_bundle_and_preview_have_no_effects"
    )
    fixture.setUp()
    owner.addCleanup(fixture.doCleanups)
    bundle = plan_gpt_card_publication_bundle(**fixture.values)
    preview = bundle.preview
    record = {
        "schema_version": trusted._TRUSTED_APPROVAL_SCHEMA,
        "receipt_id": preview.receipt_id,
        "receipt_digest": preview.receipt_digest,
        "packet_digest": preview.packet_digest,
        "paper_id": preview.paper_id,
        "run_id": preview.run_id,
        "source_manifest_sha256": preview.source_manifest_sha256,
        "request_plan_sha256": preview.request_plan_sha256,
        "summary_publication_sha256": preview.summary_publication_sha256,
        "write_manifest_sha256": preview.write_manifest_sha256,
        "provenance_sha256": preview.provenance_sha256,
        "file_count": len(preview.file_refs),
        "total_bytes": preview.total_bytes,
        "approver_id": fixture.receipt.approval.approver_id,
        "approved_at": fixture.receipt.approval.approved_at,
    }
    return fixture, bundle, record


def _write_record(path, record):
    path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


@contextmanager
def _trusted_control(control, approval):
    with (
        patch.object(trusted, "_TRUSTED_APPROVAL_PATH", approval),
        patch.object(trusted, "_TRUSTED_APPROVAL_OWNER_UID", os.getuid()),
        patch.object(reservation, "_CONTROL_DIR", control),
        patch.object(reservation, "_CONTROL_OWNER_UID", os.getuid()),
        patch.object(reservation.os, "geteuid", return_value=0),
    ):
        yield


@requires_secure_nofollow_writes
class TestGptCardOutputControls(unittest.TestCase):
    def test_exact_record_matches_and_receipt_is_consumed_once_without_paper_writes(
        self,
    ):
        fixture, bundle, record = _case(self)
        before = {p: p.read_bytes() for p in fixture.root.rglob("*") if p.is_file()}
        with tempfile.TemporaryDirectory() as tempdir:
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            _write_record(approval, record)
            with _trusted_control(control, approval):
                self.assertEqual(
                    trusted.validate_trusted_gpt_card_output_write_approval(
                        **fixture.values
                    ),
                    bundle.preview,
                )
                self.assertEqual(
                    reservation.reserve_trusted_gpt_card_output_write_receipt(
                        **fixture.values, publication_bundle=bundle
                    ),
                    bundle.preview,
                )
                with self.assertRaises(MillefeuilleContractError):
                    reservation.reserve_trusted_gpt_card_output_write_receipt(
                        **fixture.values, publication_bundle=bundle
                    )
            database = control / reservation._LEDGER_NAME
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(database) as connection:
                audits = connection.execute(
                    "SELECT audit_json FROM approvals"
                ).fetchall()
                attempts = connection.execute(
                    "SELECT status,bundle_manifest_sha256,file_count,total_bytes "
                    "FROM publication_attempts"
                ).fetchall()
            self.assertEqual(len(audits), 1)
            self.assertEqual(json.loads(audits[0][0])["status"], "consumed")
            self.assertEqual(
                attempts,
                [("committing", bundle.bundle_manifest_sha256, 5, bundle.total_bytes)],
            )
        self.assertEqual(
            before, {p: p.read_bytes() for p in fixture.root.rglob("*") if p.is_file()}
        )

    def test_missing_mismatched_noncanonical_or_writable_approval_never_creates_ledger(
        self,
    ):
        fixture, bundle, record = _case(self)
        with tempfile.TemporaryDirectory() as tempdir:
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            with _trusted_control(control, approval):
                for field, wrong in (
                    ("source_manifest_sha256", "sha256:" + "0" * 64),
                    ("request_plan_sha256", "sha256:" + "0" * 64),
                    ("summary_publication_sha256", "sha256:" + "0" * 64),
                    ("write_manifest_sha256", "sha256:" + "0" * 64),
                    ("provenance_sha256", "sha256:" + "0" * 64),
                    ("receipt_digest", "sha256:" + "0" * 64),
                    ("run_id", "other-run"),
                    ("file_count", 6),
                    ("file_count", 5.0),
                    ("total_bytes", bundle.total_bytes + 1),
                    ("total_bytes", float(bundle.total_bytes)),
                    ("extra", "invalid"),
                ):
                    with self.subTest(field=field):
                        _write_record(approval, {**record, field: wrong})
                        with self.assertRaises(MillefeuilleContractError):
                            reservation.reserve_trusted_gpt_card_output_write_receipt(
                                **fixture.values, publication_bundle=bundle
                            )
                approval.unlink()
                with self.assertRaises(MillefeuilleContractError):
                    reservation.reserve_trusted_gpt_card_output_write_receipt(
                        **fixture.values, publication_bundle=bundle
                    )
                _write_record(approval, record)
                approval.chmod(0o666)
                with self.assertRaises(MillefeuilleContractError):
                    reservation.reserve_trusted_gpt_card_output_write_receipt(
                        **fixture.values, publication_bundle=bundle
                    )
                approval.chmod(0o600)
                approval.write_text(json.dumps(record, indent=2), encoding="utf-8")
                with self.assertRaises(MillefeuilleContractError):
                    reservation.reserve_trusted_gpt_card_output_write_receipt(
                        **fixture.values, publication_bundle=bundle
                    )
                self.assertFalse((control / reservation._LEDGER_NAME).exists())

    def test_altered_bundle_or_unprivileged_reservation_has_no_effect(self):
        fixture, bundle, record = _case(self)
        with tempfile.TemporaryDirectory() as tempdir:
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            _write_record(approval, record)
            with _trusted_control(control, approval):
                with self.assertRaises(MillefeuilleContractError):
                    reservation.reserve_trusted_gpt_card_output_write_receipt(
                        **fixture.values,
                        publication_bundle=replace(
                            bundle, total_bytes=bundle.total_bytes + 1
                        ),
                    )
                with (
                    patch.object(reservation.os, "geteuid", return_value=1000),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    reservation.reserve_trusted_gpt_card_output_write_receipt(
                        **fixture.values, publication_bundle=bundle
                    )
                self.assertFalse((control / reservation._LEDGER_NAME).exists())
