"""Exact card output approval previews bind the five files without effects."""

from datetime import UTC, datetime, timedelta
import json
import unittest

from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    compute_approved_live_receipt_digest,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import (
    OperatorPreflightPacket,
    compute_operator_authorization_context_digest,
    compute_operator_preflight_packet_digest,
)
from millefeuille.domain.paper_card_output_write_scope import (
    validate_gpt_card_output_write_preview,
)
from millefeuille.domain.paper_card_publication_bundle import (
    plan_gpt_card_publication_bundle,
)
from tests import test_millefeuille_paper_card_output_plan as output_tests
from tests import test_millefeuille_summary_output_write_scope as summary_scope
from tests.platform_capabilities import requires_secure_nofollow_writes


def _write_pair(root, plan, *, changes=None):
    packet, receipt = summary_scope._write_approval_pair(
        root,
        manifest_sha256=plan.write_manifest_sha256,
        observed_usage_sha256=plan.provenance_sha256,
        provenance_manifest_sha256=plan.provenance_sha256,
        paper_id=plan.paper_id,
        run_id=plan.run_id,
    )
    p, r = packet.to_dict(), receipt.to_dict()
    p.pop("integrity")
    r.pop("integrity")
    p["packet_id"] = "packet-gpt-card-write-1"
    p["approval_receipt"] = None
    p["targets"] = [
        row for row in p["targets"] if row["kind"] in {"source-pack-root", "paper-id"}
    ] + [
        {"kind": "analysis-run", "id": plan.run_id},
        {"kind": "card-output-manifest", "id": plan.write_manifest_sha256},
        {"kind": "card-request-manifest", "id": plan.request_plan_sha256},
        {"kind": "summary-publication", "id": plan.summary_publication_sha256},
    ]
    p.update(changes or {})
    p["targets"].sort(key=lambda row: (row["kind"], row["id"]))
    p["targets"].append(
        {
            "kind": "preflight-scope",
            "id": compute_operator_authorization_context_digest(p),
        }
    )
    p["targets"].sort(key=lambda row: (row["kind"], row["id"]))
    r["receipt_id"] = "receipt-gpt-card-write-1"
    r["run_id"] = p["run_id"]
    r["scope"].update(
        operations=p["operations"],
        targets=p["targets"],
        limits=p["limits"],
        selector=p["source"]["selector"],
        stop_conditions=p["stop_conditions"],
    )
    r["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_approved_live_receipt_digest(r),
    }
    p["approval_receipt"] = {
        "receipt_id": r["receipt_id"],
        "content_digest": r["integrity"]["content_digest"],
    }
    p["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_operator_preflight_packet_digest(p),
    }
    return OperatorPreflightPacket.from_dict(p), ApprovedLiveReceipt.from_dict(r)


@requires_secure_nofollow_writes
class TestGptCardOutputWriteScope(unittest.TestCase):
    def setUp(self):
        fixture = output_tests.TestGptCardOutputPlan(
            "test_plans_canonical_files_without_writes_or_false_downstream_status"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.values["source_pack_root"]
        self.plan = fixture._plan()
        self.packet, self.receipt = _write_pair(self.root, self.plan)
        self.values = dict(
            **fixture.values,
            outcome=fixture.outcome,
            packet=self.packet,
            receipt=self.receipt,
        )

    def test_exact_bundle_and_preview_have_no_effects(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        preview = validate_gpt_card_output_write_preview(**self.values)
        bundle = plan_gpt_card_publication_bundle(**self.values)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(bundle.preview, preview)
        self.assertEqual(bundle.files, self.plan.files)
        self.assertEqual(len(preview.file_refs), 5)
        self.assertEqual(bundle.total_bytes, self.plan.total_bytes)
        self.assertEqual(preview.files_written, 0)
        self.assertNotIn("Private", repr(bundle))
        manifest = json.loads(bundle.bundle_manifest_json)
        self.assertEqual(manifest["packet_digest"], self.packet.content_digest)
        self.assertEqual(manifest["receipt_digest"], self.receipt.content_digest)
        self.assertEqual(
            manifest["entries"],
            [
                {"ref": item.ref, "sha256": item.sha256, "bytes": len(item.data)}
                for item in self.plan.files
            ],
        )

    def test_rejects_broader_write_call_budget_run_and_destination(self):
        for change in (
            {"operations": ["source-pack.write", "model.card"]},
            {"limits": {"max_provider_calls": 1, "max_cost_usd_micros": 0}},
            {"limits": {"max_provider_calls": 0, "max_cost_usd_micros": 1}},
            {"run_id": "other-run"},
            {"stop_conditions": ["write-failure"]},
            {
                "destinations": [
                    {"kind": "source-pack-root", "id": "sha256:" + "0" * 64}
                ]
            },
            {"targets": [{"kind": "paper-id", "id": self.plan.paper_id}]},
        ):
            with (
                self.subTest(change=change),
                self.assertRaises(MillefeuilleContractError),
            ):
                packet, receipt = _write_pair(self.root, self.plan, changes=change)
                validate_gpt_card_output_write_preview(
                    **{**self.values, "packet": packet, "receipt": receipt}
                )

    def test_expiry_source_change_and_summary_call_receipt_fail_closed(self):
        with self.assertRaises(MillefeuilleContractError):
            validate_gpt_card_output_write_preview(
                **self.values, now=datetime.now(UTC) + timedelta(hours=2)
            )
        with self.assertRaises(MillefeuilleContractError):
            validate_gpt_card_output_write_preview(
                **{
                    **self.values,
                    "packet": self.fixture.fixture.packet,
                    "receipt": self.fixture.fixture.receipt,
                }
            )
        path = (
            self.root
            / f"analyses/millefeuille/{self.values['publication'].run_id}"
            / "structure/inputs/selected.md"
        )
        path.write_bytes(path.read_bytes() + b"drift")
        with self.assertRaises(MillefeuilleContractError):
            plan_gpt_card_publication_bundle(**self.values)
