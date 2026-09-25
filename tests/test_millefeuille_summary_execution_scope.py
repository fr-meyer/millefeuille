"""Offline approval-preview tests for a grounded GPT summary batch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.live_receipts import (
    APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
    ApprovedLiveReceipt,
    compute_approved_live_receipt_digest,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import (
    OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
    OperatorPreflightPacket,
    compute_operator_authorization_context_digest,
    compute_operator_preflight_packet_digest,
    compute_operator_root_target_id,
)
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from tests import test_millefeuille_summary_dispatch as dispatch_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


def _approved_pair(
    root: Path,
    manifest_sha256: str,
    paper_id: str,
    calls: int,
    *,
    model: str = "gpt-5.6-sol",
    approved_at: datetime | None = None,
):
    now = (approved_at or datetime.now(UTC)).replace(microsecond=0)
    root_text = str(root)
    destination = {
        "kind": "artifact-root",
        "id": compute_operator_root_target_id(root_text),
    }
    packet = {
        "schema_version": OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
        "packet_id": "packet-gpt-summary-1",
        "mode": "approved-live",
        "source": {
            "adapter": "local-fixture",
            "selector": {"kind": "batch-manifest", "value": manifest_sha256},
            "item_cap": 1,
            "resolved_item_count": 1,
        },
        "run_id": "run-gpt-summary-1",
        "roots": {"artifact_root": root_text, "source_pack_root": root_text},
        "operations": ["model.summarize"],
        "targets": [],
        "destinations": [destination],
        "provider": {
            "provider_id": "openai",
            "model_id": model,
            "profile_id": "openclaw-subscription-oauth",
        },
        "limits": {"max_provider_calls": calls, "max_cost_usd_micros": 0},
        "disposal_policy": {
            "pdfs": "not-applicable",
            "provider_payloads": "never-persist",
            "temporary_files": "delete-after-run",
        },
        "stop_conditions": [
            "auth-failure",
            "model-mismatch",
            "provider-error",
            "schema-failure",
            "source-drift",
        ],
        "rollback_actions": ["stop-and-review"],
        "acceptance_status": "not-applicable",
        "credential_requirements": [
            {"type": "model-oauth", "reference": "OPENCLAW_CODEX_OAUTH_READY"}
        ],
        "approval_receipt": None,
    }
    packet["targets"] = sorted(
        [
            destination,
            {"kind": "paper-id", "id": paper_id},
            {"kind": "summary-manifest", "id": manifest_sha256},
        ],
        key=lambda row: (row["kind"], row["id"]),
    )
    packet["targets"].append(
        {
            "kind": "preflight-scope",
            "id": compute_operator_authorization_context_digest(packet),
        }
    )
    packet["targets"].sort(key=lambda row: (row["kind"], row["id"]))
    receipt = {
        "schema_version": APPROVED_LIVE_RECEIPT_SCHEMA_VERSION,
        "receipt_id": "receipt-gpt-summary-1",
        "run_id": packet["run_id"],
        "approval": {
            "approver_id": "fr-meyer",
            "approved_at": now.isoformat().replace("+00:00", "Z"),
        },
        "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "scope": {
            "operations": packet["operations"],
            "targets": packet["targets"],
            "max_items": 1,
            "selector": packet["source"]["selector"],
            "output_root": root_text,
            "source_pack_root": root_text,
            "provider": {"provider_id": "openai", "model_id": model},
            "limits": packet["limits"],
            "disposal_policy": packet["disposal_policy"],
            "stop_conditions": packet["stop_conditions"],
        },
    }
    receipt["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_approved_live_receipt_digest(receipt),
    }
    packet["approval_receipt"] = {
        "receipt_id": receipt["receipt_id"],
        "content_digest": receipt["integrity"]["content_digest"],
    }
    packet["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_operator_preflight_packet_digest(packet),
    }
    return OperatorPreflightPacket.from_dict(packet), ApprovedLiveReceipt.from_dict(
        receipt
    )


@requires_secure_nofollow_writes
class TestSummaryExecutionScope(unittest.TestCase):
    def _case(self, root: Path, *, no_sections: bool = False):
        markdown = (
            "# Page 1\nPrivate text.\n"
            if no_sections
            else "# Page 1\n# Introduction\nPrivate text.\n"
        )
        route, structure, preparation = dispatch_tests.TestSummaryDispatch()._fixture(
            root, markdown_text=markdown
        )
        evidence = {
            "route_evidence_path": route,
            "structure_evidence_path": structure,
            "preparation_path": preparation,
        }
        plan = plan_grounded_gpt_summary_batch(**evidence)
        packet, receipt = _approved_pair(
            root, plan.manifest.sha256, plan.batch.paper_id, len(plan.batch.units)
        )
        return evidence, plan, packet, receipt

    def test_exact_scope_is_previewed_without_effects(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            evidence, plan, packet, receipt = self._case(root)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            preview = validate_gpt_summary_approval_preview(
                **evidence,
                artifact_root=root,
                source_pack_root=root,
                packet=packet,
                receipt=receipt,
            )
            after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(preview.manifest_sha256, plan.manifest.sha256)
            self.assertEqual(preview.request_count, 3)
            self.assertEqual(preview.provider_calls_performed, 0)
            self.assertNotIn("Private text", repr(preview))

    def test_zero_section_plan_uses_two_call_scope(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            evidence, _, packet, receipt = self._case(root, no_sections=True)
            preview = validate_gpt_summary_approval_preview(
                **evidence,
                artifact_root=root,
                source_pack_root=root,
                packet=packet,
                receipt=receipt,
            )
            self.assertEqual(preview.request_count, 2)
            self.assertEqual(packet.max_provider_calls, 2)

    def test_rejects_scope_drift_and_expiry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            evidence, plan, packet, receipt = self._case(root)
            wrong_manifest, wrong_receipt = _approved_pair(
                root, "sha256:" + "0" * 64, plan.batch.paper_id, 3
            )
            wrong_calls, wrong_calls_receipt = _approved_pair(
                root, plan.manifest.sha256, plan.batch.paper_id, 4
            )
            wrong_model, wrong_model_receipt = _approved_pair(
                root,
                plan.manifest.sha256,
                plan.batch.paper_id,
                3,
                model="gpt-5.6-terra",
            )
            for candidate_packet, candidate_receipt in (
                (wrong_manifest, wrong_receipt),
                (wrong_calls, wrong_calls_receipt),
                (wrong_model, wrong_model_receipt),
            ):
                with (
                    self.subTest(packet=candidate_packet.packet_id),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    validate_gpt_summary_approval_preview(
                        **evidence,
                        artifact_root=root,
                        source_pack_root=root,
                        packet=candidate_packet,
                        receipt=candidate_receipt,
                    )
            with self.assertRaises(MillefeuilleContractError):
                validate_gpt_summary_approval_preview(
                    **evidence,
                    artifact_root=root,
                    source_pack_root=root,
                    packet=packet,
                    receipt=receipt,
                    now=datetime.now(UTC) + timedelta(hours=2),
                )

    def test_replans_source_and_rejects_changed_text(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            evidence, _, packet, receipt = self._case(root)
            (root / "selected.md").write_text("Changed text.\n", encoding="utf-8")
            with self.assertRaises(MillefeuilleContractError):
                validate_gpt_summary_approval_preview(
                    **evidence,
                    artifact_root=root,
                    source_pack_root=root,
                    packet=packet,
                    receipt=receipt,
                )
