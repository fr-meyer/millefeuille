"""Offline checks for the administrator-owned GPT summary approval record."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import summary_trusted_approval as trusted
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from tests import test_millefeuille_summary_dispatch as dispatch_tests
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_summary_execution_scope import _approved_pair


def _case(root: Path):
    route, structure, preparation = dispatch_tests.TestSummaryDispatch()._fixture(
        root, markdown_text="# Page 1\n# Introduction\nPrivate text.\n"
    )
    evidence = {
        "route_evidence_path": route,
        "structure_evidence_path": structure,
        "preparation_path": preparation,
        "artifact_root": root,
        "source_pack_root": root,
    }
    plan = plan_grounded_gpt_summary_batch(
        route_evidence_path=route,
        structure_evidence_path=structure,
        preparation_path=preparation,
    )
    packet, receipt = _approved_pair(
        root, plan.manifest.sha256, plan.batch.paper_id, len(plan.batch.units)
    )
    preview = validate_gpt_summary_approval_preview(
        **evidence, packet=packet, receipt=receipt
    )
    record = {
        "schema_version": trusted._TRUSTED_APPROVAL_SCHEMA,
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.content_digest,
        "packet_digest": packet.content_digest,
        "manifest_sha256": preview.manifest_sha256,
        "paper_id": preview.paper_id,
        "request_count": preview.request_count,
        "approver_id": receipt.approval.approver_id,
        "approved_at": receipt.approval.approved_at,
    }
    return evidence, packet, receipt, record


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
class TestTrustedGptSummaryApproval(unittest.TestCase):
    def test_exact_administrator_record_matches_without_effects(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "paper"
            root.mkdir()
            evidence, packet, receipt, record = _case(root)
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            path = control / "approval.json"
            _write_record(path, record)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with _approval_path(path):
                preview = trusted.validate_trusted_gpt_summary_approval(
                    **evidence, packet=packet, receipt=receipt
                )
            after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(preview.request_count, 3)
            self.assertEqual(preview.provider_calls_performed, 0)

    def test_missing_or_tampered_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "paper"
            root.mkdir()
            evidence, packet, receipt, record = _case(root)
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            path = control / "approval.json"
            with _approval_path(path):
                with self.assertRaises(MillefeuilleContractError):
                    trusted.validate_trusted_gpt_summary_approval(
                        **evidence, packet=packet, receipt=receipt
                    )
                for field, wrong in (
                    ("manifest_sha256", "sha256:" + "0" * 64),
                    ("receipt_digest", "sha256:" + "0" * 64),
                    ("request_count", 4),
                    ("request_count", 3.0),
                    ("paper_id", "other-paper"),
                ):
                    with self.subTest(field=field):
                        altered = {**record, field: wrong}
                        _write_record(path, altered)
                        with self.assertRaises(MillefeuilleContractError):
                            trusted.validate_trusted_gpt_summary_approval(
                                **evidence, packet=packet, receipt=receipt
                            )

    def test_writable_or_linked_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "paper"
            root.mkdir()
            evidence, packet, receipt, record = _case(root)
            control = Path(tempdir) / "control"
            control.mkdir(mode=0o700)
            path = control / "approval.json"
            _write_record(path, record)
            with _approval_path(path):
                path.chmod(0o666)
                with self.assertRaises(MillefeuilleContractError):
                    trusted.validate_trusted_gpt_summary_approval(
                        **evidence, packet=packet, receipt=receipt
                    )
                path.chmod(0o600)
                link = control / "link.json"
                link.symlink_to(path)
            with (
                _approval_path(link),
                self.assertRaises(MillefeuilleContractError),
            ):
                trusted.validate_trusted_gpt_summary_approval(
                    **evidence, packet=packet, receipt=receipt
                )
            with (
                patch.object(trusted, "_TRUSTED_APPROVAL_PATH", path),
                patch.object(trusted, "_TRUSTED_APPROVAL_OWNER_UID", os.getuid() + 1),
                self.assertRaises(MillefeuilleContractError),
            ):
                trusted.validate_trusted_gpt_summary_approval(
                    **evidence, packet=packet, receipt=receipt
                )
