"""No-write MF-100 scope checks for a complete GPT summary output bundle."""

from __future__ import annotations

from dataclasses import replace
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
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_output_plan import plan_gpt_summary_outputs
from millefeuille.domain.summary_output_write_scope import (
    validate_gpt_summary_output_write_preview,
)
from millefeuille.domain.summary_results import accept_summary_execution_batch
from tests import test_millefeuille_summary_results as result_tests
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_summary_trusted_approval import _case


def _write_approval_pair(
    root: Path,
    *,
    manifest_sha256: str,
    paper_id: str,
    run_id: str,
    approved_at: datetime | None = None,
) -> tuple[OperatorPreflightPacket, ApprovedLiveReceipt]:
    now = (approved_at or datetime.now(UTC)).replace(microsecond=0)
    root_text = str(root)
    destination = {
        "kind": "source-pack-root",
        "id": compute_operator_root_target_id(root_text),
    }
    packet = {
        "schema_version": OPERATOR_PREFLIGHT_PACKET_SCHEMA_VERSION,
        "packet_id": "packet-gpt-summary-write-1",
        "mode": "approved-live",
        "source": {
            "adapter": "source-pack",
            "selector": {"kind": "batch-manifest", "value": manifest_sha256},
            "item_cap": 1,
            "resolved_item_count": 1,
        },
        "run_id": run_id,
        "roots": {"artifact_root": root_text, "source_pack_root": root_text},
        "operations": ["source-pack.write"],
        "targets": [],
        "destinations": [destination],
        "provider": None,
        "limits": {"max_provider_calls": 0, "max_cost_usd_micros": 0},
        "disposal_policy": {
            "pdfs": "not-applicable",
            "provider_payloads": "not-applicable",
            "temporary_files": "delete-after-run",
        },
        "stop_conditions": ["source-drift", "write-failure"],
        "rollback_actions": ["stop-and-review"],
        "acceptance_status": "not-applicable",
        "credential_requirements": [],
        "approval_receipt": None,
    }
    packet["targets"] = sorted(
        [
            destination,
            {"kind": "paper-id", "id": paper_id},
            {"kind": "summary-output-manifest", "id": manifest_sha256},
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
        "receipt_id": "receipt-gpt-summary-write-1",
        "run_id": run_id,
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
            "provider": None,
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
class TestGptSummaryOutputWriteScope(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "paper"
        self.root.mkdir()
        self.evidence, execution_packet, execution_receipt, _record = _case(self.root)
        batch = plan_grounded_gpt_summary_batch(
            route_evidence_path=self.evidence["route_evidence_path"],
            structure_evidence_path=self.evidence["structure_evidence_path"],
            preparation_path=self.evidence["preparation_path"],
        ).batch
        approval = validate_gpt_summary_approval_preview(
            **self.evidence, packet=execution_packet, receipt=execution_receipt
        )
        executions = tuple(
            result_tests.TestSummaryResults._execution(
                unit, result_tests.TestSummaryResults._output(unit)
            )
            for unit in batch.units
        )
        accepted = accept_summary_execution_batch(
            batch=batch,
            executions={
                (unit.stage, unit.unit_id): execution
                for unit, execution in zip(batch.units, executions, strict=True)
            },
            route_evidence_path=self.evidence["route_evidence_path"],
            structure_evidence_path=self.evidence["structure_evidence_path"],
            preparation_path=self.evidence["preparation_path"],
        )
        self.outcome = TrustedGptSummaryOutcome(
            approval=approval,
            accepted=accepted,
            batch=batch,
            executions=executions,
        )
        self.run_id = "run-gpt-write-1"
        self.plan = plan_gpt_summary_outputs(
            outcome=self.outcome,
            run_id=self.run_id,
            route_evidence_path=self.evidence["route_evidence_path"],
            structure_evidence_path=self.evidence["structure_evidence_path"],
            preparation_path=self.evidence["preparation_path"],
        )
        self.packet, self.receipt = _write_approval_pair(
            self.root,
            manifest_sha256=self.plan.write_manifest_sha256,
            paper_id=self.plan.paper_id,
            run_id=self.run_id,
        )

    def _preview(self, **overrides):
        values = {
            "outcome": self.outcome,
            "run_id": self.run_id,
            "route_evidence_path": self.evidence["route_evidence_path"],
            "structure_evidence_path": self.evidence["structure_evidence_path"],
            "preparation_path": self.evidence["preparation_path"],
            "source_pack_root": self.root,
            "packet": self.packet,
            "receipt": self.receipt,
        }
        values.update(overrides)
        return validate_gpt_summary_output_write_preview(**values)

    def test_exact_write_scope_is_previewed_without_effects(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        preview = self._preview()
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(preview.write_manifest_sha256, self.plan.write_manifest_sha256)
        self.assertEqual(len(preview.file_refs), len(self.plan.texts) + 2)
        self.assertIn(self.plan.write_manifest_ref, preview.file_refs)
        self.assertIn(self.plan.summary_record_ref, preview.file_refs)
        self.assertEqual(preview.files_written, 0)
        self.assertNotIn("Private accepted summary", repr(preview))

    def test_scope_drift_expiry_and_source_change_fail_closed(self):
        wrong_packet, wrong_receipt = _write_approval_pair(
            self.root,
            manifest_sha256="sha256:" + "0" * 64,
            paper_id=self.plan.paper_id,
            run_id=self.run_id,
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "write scope"):
            self._preview(packet=wrong_packet, receipt=wrong_receipt)
        with self.assertRaises(MillefeuilleContractError):
            self._preview(now=datetime.now(UTC) + timedelta(hours=2))
        (self.root / "selected.md").write_text("Changed source.\n", encoding="utf-8")
        with self.assertRaises(MillefeuilleContractError):
            self._preview()

    def test_changed_accepted_text_and_wrong_root_are_rejected(self):
        unit = replace(self.outcome.accepted.units[0], summary="Fabricated")
        accepted = replace(
            self.outcome.accepted,
            units=(unit, *self.outcome.accepted.units[1:]),
        )
        with self.assertRaises(MillefeuilleContractError):
            self._preview(outcome=replace(self.outcome, accepted=accepted))
        with self.assertRaises(MillefeuilleContractError):
            self._preview(source_pack_root=self.root.parent)
