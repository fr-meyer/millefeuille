"""Offline administrator approval checks for one GPT summary output write."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import summary_output_trusted_approval as trusted
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_output_write_scope import (
    validate_gpt_summary_output_write_preview,
)
from tests import test_millefeuille_summary_output_write_scope as scope_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


def _case(owner: unittest.TestCase):
    fixture = scope_tests.TestGptSummaryOutputWriteScope(
        "test_exact_write_scope_is_previewed_without_effects"
    )
    fixture.setUp()
    owner.addCleanup(fixture.doCleanups)
    evidence = {
        "outcome": fixture.outcome,
        "run_id": fixture.run_id,
        "route_evidence_path": fixture.evidence["route_evidence_path"],
        "structure_evidence_path": fixture.evidence["structure_evidence_path"],
        "preparation_path": fixture.evidence["preparation_path"],
        "source_pack_root": fixture.root,
        "packet": fixture.packet,
        "receipt": fixture.receipt,
    }
    preview = validate_gpt_summary_output_write_preview(**evidence)
    record = {
        "schema_version": trusted._TRUSTED_APPROVAL_SCHEMA,
        "receipt_id": fixture.receipt.receipt_id,
        "receipt_digest": preview.receipt_digest,
        "packet_digest": preview.packet_digest,
        "source_manifest_sha256": preview.source_manifest_sha256,
        "write_manifest_sha256": preview.write_manifest_sha256,
        "observed_usage_sha256": preview.observed_usage_sha256,
        "provenance_manifest_sha256": preview.provenance_manifest_sha256,
        "paper_id": preview.paper_id,
        "run_id": preview.run_id,
        "file_count": len(preview.file_refs),
        "approver_id": fixture.receipt.approval.approver_id,
        "approved_at": fixture.receipt.approval.approved_at,
    }
    return fixture.root, evidence, record


@contextmanager
def _approval_path(path: Path):
    with (
        patch.object(trusted, "_TRUSTED_APPROVAL_PATH", path),
        patch.object(trusted, "_TRUSTED_APPROVAL_OWNER_UID", os.getuid()),
    ):
        yield


def _write_record(path: Path, record: dict):
    path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


@requires_secure_nofollow_writes
class TestGptSummaryOutputTrustedApproval(unittest.TestCase):
    def test_exact_record_matches_without_paper_write(self):
        root, evidence, record = _case(self)
        with tempfile.TemporaryDirectory() as tempdir:
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            _write_record(approval, record)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with _approval_path(approval):
                preview = trusted.validate_trusted_gpt_summary_output_write_approval(
                    **evidence
                )
            after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(preview.files_written, 0)
            self.assertEqual(
                preview.write_manifest_sha256, record["write_manifest_sha256"]
            )

    def test_missing_or_changed_record_is_rejected(self):
        _root, evidence, record = _case(self)
        with tempfile.TemporaryDirectory() as tempdir:
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            with _approval_path(approval):
                with self.assertRaises(MillefeuilleContractError):
                    trusted.validate_trusted_gpt_summary_output_write_approval(
                        **evidence
                    )
                for field, wrong in (
                    ("write_manifest_sha256", "sha256:" + "0" * 64),
                    ("observed_usage_sha256", "sha256:" + "0" * 64),
                    ("provenance_manifest_sha256", "sha256:" + "0" * 64),
                    ("receipt_digest", "sha256:" + "0" * 64),
                    ("paper_id", "other-paper"),
                    ("run_id", "other-run"),
                    ("file_count", record["file_count"] + 1),
                    ("file_count", float(record["file_count"])),
                ):
                    with self.subTest(field=field):
                        _write_record(approval, {**record, field: wrong})
                        with self.assertRaises(MillefeuilleContractError):
                            trusted.validate_trusted_gpt_summary_output_write_approval(
                                **evidence
                            )

    def test_writable_or_linked_record_is_rejected(self):
        _root, evidence, record = _case(self)
        with tempfile.TemporaryDirectory() as tempdir:
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            approval = control / "approval.json"
            _write_record(approval, record)
            with _approval_path(approval):
                approval.chmod(0o666)
                with self.assertRaises(MillefeuilleContractError):
                    trusted.validate_trusted_gpt_summary_output_write_approval(
                        **evidence
                    )
                approval.chmod(0o600)
                linked = control / "linked.json"
                linked.symlink_to(approval)
            with (
                _approval_path(linked),
                self.assertRaises(MillefeuilleContractError),
            ):
                trusted.validate_trusted_gpt_summary_output_write_approval(**evidence)
