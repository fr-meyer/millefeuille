"""One-call GPT card approval preview never reserves or grants authority."""

from datetime import UTC, datetime, timedelta
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
from millefeuille.domain.paper_card_execution_scope import (
    CARD_EXECUTION_STOP_CONDITIONS,
    validate_gpt_card_approval_preview,
)
from millefeuille.domain.paper_card_inputs import plan_published_gpt_paper_card_request
from tests import test_millefeuille_summary_execution_scope as summary_scope
from tests import test_millefeuille_summary_published_handoff as handoff_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


def _card_pair(root, plan, *, changes=None, approved_at=None):
    original_packet, original_receipt = summary_scope._approved_pair(
        root, plan.manifest_sha256, plan.paper_id, 1, approved_at=approved_at
    )
    packet = original_packet.to_dict()
    packet.pop("integrity")
    packet["packet_id"] = "packet-gpt-card-1"
    packet["operations"] = ["model.card"]
    packet["run_id"] = plan.run_id
    packet["stop_conditions"] = list(CARD_EXECUTION_STOP_CONDITIONS)
    packet["approval_receipt"] = None
    packet["targets"] = [
        row for row in packet["targets"] if row["kind"] in {"artifact-root", "paper-id"}
    ] + [
        {"kind": "analysis-run", "id": plan.run_id},
        {"kind": "card-manifest", "id": plan.manifest_sha256},
        {"kind": "summary-publication", "id": plan.publication_manifest_sha256},
    ]
    packet.update(changes or {})
    packet["targets"].sort(key=lambda row: (row["kind"], row["id"]))
    packet["targets"].append(
        {
            "kind": "preflight-scope",
            "id": compute_operator_authorization_context_digest(packet),
        }
    )
    packet["targets"].sort(key=lambda row: (row["kind"], row["id"]))
    receipt = original_receipt.to_dict()
    receipt.pop("integrity")
    receipt["receipt_id"] = "receipt-gpt-card-1"
    receipt["run_id"] = packet["run_id"]
    receipt["scope"].update(
        {
            "operations": packet["operations"],
            "targets": packet["targets"],
            "selector": packet["source"]["selector"],
            "limits": packet["limits"],
            "disposal_policy": packet["disposal_policy"],
            "stop_conditions": packet["stop_conditions"],
            "provider": {
                key: packet["provider"][key] for key in ("provider_id", "model_id")
            },
        }
    )
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
class TestGptCardApprovalScope(unittest.TestCase):
    def setUp(self):
        fixture = handoff_tests.TestPublishedSummaryHandoff(
            "test_handoff_matches_source_pack_and_has_no_effects_or_private_text"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.fixture.root
        self.values = dict(
            source_pack_root=self.root,
            artifact_root=self.root,
            publication=fixture.publication,
            run_id="run-card-02",
            **fixture.evidence,
        )
        self.plan = plan_published_gpt_paper_card_request(
            **{
                key: value
                for key, value in self.values.items()
                if key != "artifact_root"
            }
        )

    def _preview(self, packet, receipt, **overrides):
        return validate_gpt_card_approval_preview(
            **{**self.values, **overrides}, packet=packet, receipt=receipt
        )

    def test_exact_one_call_scope_has_no_effects(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        packet, receipt = _card_pair(self.root, self.plan)
        preview = self._preview(packet, receipt)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(preview.manifest_sha256, self.plan.manifest_sha256)
        self.assertEqual(
            preview.summary_publication_sha256,
            self.fixture.publication.bundle_manifest_sha256,
        )
        self.assertEqual(preview.run_id, self.plan.run_id)
        self.assertEqual(preview.request_count, 1)
        self.assertEqual(
            (preview.provider_calls_performed, preview.writes_performed), (0, 0)
        )
        self.assertNotIn("Private", repr(preview))

    def test_rejects_broader_operation_run_model_and_budget(self):
        changes = (
            {"operations": ["model.summarize"]},
            {"operations": ["model.card", "model.classify"]},
            {"run_id": "other-run"},
            {"limits": {"max_provider_calls": 2, "max_cost_usd_micros": 0}},
            {"limits": {"max_provider_calls": 1, "max_cost_usd_micros": 1}},
            {
                "provider": {
                    "provider_id": "openai",
                    "model_id": "gpt-5",
                    "profile_id": "openclaw-subscription-oauth",
                }
            },
            {"stop_conditions": ["source-drift"]},
            {"acceptance_status": "passed"},
        )
        for change in changes:
            with (
                self.subTest(change=change),
                self.assertRaises(MillefeuilleContractError),
            ):
                self._preview(*_card_pair(self.root, self.plan, changes=change))

    def test_rejects_source_selector_target_and_destination_drift(self):
        for change in (
            {
                "source": {
                    "adapter": "local-fixture",
                    "selector": {
                        "kind": "batch-manifest",
                        "value": "sha256:" + "0" * 64,
                    },
                    "item_cap": 1,
                    "resolved_item_count": 1,
                }
            },
            {
                "targets": [
                    {
                        "kind": "artifact-root",
                        "id": self.fixture.publication.root_target_id,
                    }
                ]
            },
            {"destinations": [{"kind": "artifact-root", "id": "sha256:" + "0" * 64}]},
        ):
            with (
                self.subTest(change=change),
                self.assertRaises(MillefeuilleContractError),
            ):
                self._preview(*_card_pair(self.root, self.plan, changes=change))
        with self.assertRaises(MillefeuilleContractError):
            self._preview(
                *_card_pair(self.root, self.plan), artifact_root=self.root / "other"
            )

    def test_rejects_expired_receipt_and_changed_source_before_authority(self):
        packet, receipt = _card_pair(self.root, self.plan)
        with self.assertRaises(MillefeuilleContractError):
            self._preview(packet, receipt, now=datetime.now(UTC) + timedelta(hours=2))
        target = self.root / self.fixture.fixture.plan.texts[0].ref
        target.write_bytes(target.read_bytes() + b"changed")
        with self.assertRaises(MillefeuilleContractError):
            self._preview(packet, receipt)

    def test_summary_receipt_cannot_authorize_card_preview(self):
        packet, receipt = summary_scope._approved_pair(
            self.root, self.plan.manifest_sha256, self.plan.paper_id, 1
        )
        with self.assertRaises(MillefeuilleContractError):
            self._preview(packet, receipt)
