"""Offline root-broker state checks for exact GPT summary publication."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import summary_output_replay_reservation as reservation
from millefeuille.domain import summary_output_trusted_approval as trusted
from millefeuille.domain import summary_publication_broker as broker
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_outcome_handoff import (
    encode_gpt_summary_outcome_handoff,
)
from millefeuille.domain.summary_publication_fs import (
    GptSummaryFilesystemCommit,
    GptSummaryPublicationError,
)
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_summary_output_trusted_approval import (
    _case,
    _write_record,
)


@contextmanager
def _trusted_control(control: Path, approval: Path):
    with (
        patch.object(broker.os, "geteuid", return_value=0),
        patch.object(reservation, "_CONTROL_DIR", control),
        patch.object(reservation, "_CONTROL_OWNER_UID", os.getuid()),
        patch.object(broker, "_CONTROL_DIR", control),
        patch.object(trusted, "_TRUSTED_APPROVAL_PATH", approval),
        patch.object(trusted, "_TRUSTED_APPROVAL_OWNER_UID", os.getuid()),
    ):
        yield


@requires_secure_nofollow_writes
class TestGptSummaryPublicationBroker(unittest.TestCase):
    def setUp(self):
        root, evidence, record = _case(self)
        self.root = root
        self.evidence = evidence
        self.record = record
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.control = Path(temporary.name) / "control"
        self.control.mkdir(mode=0o700)
        self.approval = self.control / "approval.json"
        _write_record(self.approval, record)
        self.handoff = encode_gpt_summary_outcome_handoff(evidence["outcome"])

    def _publish(self):
        request = {k: v for k, v in self.evidence.items() if k != "outcome"}
        return broker.publish_trusted_gpt_summary_handoff(
            encoded_outcome=self.handoff,
            execution_approval=self.evidence["outcome"].approval,
            **request,
        )

    def _status(self):
        with sqlite3.connect(self.control / "gpt-summary-write.sqlite3") as db:
            return db.execute("SELECT status FROM publication_attempts").fetchone()[0]

    def test_approved_handoff_reserves_once_and_audits_publication(self):
        expected = GptSummaryFilesystemCommit(
            run_id=self.evidence["run_id"],
            file_count=self.record["file_count"],
            total_bytes=1,
            bundle_manifest_sha256="sha256:" + "0" * 64,
            source_pack_root=str(self.root),
        )
        with (
            _trusted_control(self.control, self.approval),
            patch.object(
                broker, "commit_prevalidated_gpt_summary_bundle", return_value=expected
            ) as commit,
        ):
            self.assertEqual(self._publish(), expected)
            with self.assertRaises(MillefeuilleContractError):
                self._publish()
        self.assertEqual(commit.call_count, 1)
        self.assertEqual(self._status(), "published")
        self.assertFalse((self.root / "analyses").exists())

    def test_missing_approval_fails_without_a_ledger_or_paper_write(self):
        self.approval.unlink()
        with (
            _trusted_control(self.control, self.approval),
            patch.object(broker, "commit_prevalidated_gpt_summary_bundle") as commit,
            self.assertRaises(MillefeuilleContractError),
        ):
            self._publish()
        commit.assert_not_called()
        self.assertFalse((self.control / "gpt-summary-write.sqlite3").exists())

    def test_invalid_handoff_fails_before_receipt_reservation(self):
        self.handoff = b"{}"
        with (
            _trusted_control(self.control, self.approval),
            patch.object(broker, "commit_prevalidated_gpt_summary_bundle") as commit,
            self.assertRaises(MillefeuilleContractError),
        ):
            self._publish()
        commit.assert_not_called()
        self.assertFalse((self.control / "gpt-summary-write.sqlite3").exists())

    def test_postcommit_audit_failure_reports_uncertainty(self):
        with (
            _trusted_control(self.control, self.approval),
            patch.object(broker, "commit_prevalidated_gpt_summary_bundle"),
            patch.object(broker, "_finish_attempt", side_effect=OSError("injected")),
            self.assertRaises(GptSummaryPublicationError) as caught,
        ):
            self._publish()
        self.assertTrue(caught.exception.committed)
        self.assertEqual(self._status(), "committing")

    def test_precommit_failure_and_postcommit_uncertainty_are_distinct(self):
        for committed, status in (
            (False, "failed-before-commit"),
            (True, "uncertain"),
        ):
            with self.subTest(committed=committed):
                root, evidence, record = _case(self)
                with tempfile.TemporaryDirectory() as tempdir:
                    control = Path(tempdir) / "control"
                    control.mkdir(mode=0o700)
                    approval = control / "approval.json"
                    _write_record(approval, record)
                    handoff = encode_gpt_summary_outcome_handoff(evidence["outcome"])
                    request = {k: v for k, v in evidence.items() if k != "outcome"}
                    with (
                        _trusted_control(control, approval),
                        patch.object(
                            broker,
                            "commit_prevalidated_gpt_summary_bundle",
                            side_effect=GptSummaryPublicationError(
                                committed=committed, cleanup_complete=True
                            ),
                        ),
                        self.assertRaises(GptSummaryPublicationError),
                    ):
                        broker.publish_trusted_gpt_summary_handoff(
                            encoded_outcome=handoff,
                            execution_approval=evidence["outcome"].approval,
                            **request,
                        )
                    with sqlite3.connect(control / "gpt-summary-write.sqlite3") as db:
                        actual = db.execute(
                            "SELECT status FROM publication_attempts"
                        ).fetchone()[0]
                    self.assertEqual(actual, status)
                    self.assertFalse((root / "analyses").exists())
