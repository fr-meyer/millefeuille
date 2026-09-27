"""Reviewed duplicates retain matches and require exact lineage and authority."""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import tempfile
import unittest

from millefeuille.domain.acceptance import _build_acceptance_summary
from millefeuille.domain.card_index_contract import canonical_json_bytes
from millefeuille.domain.duplicate_review import (
    AUDIT_REF,
    RECEIPT_REF,
    REVIEW_REF,
    SCHEMA_VERSION,
    duplicate_review_request,
    load_duplicate_profile_review,
    review_digest,
)
from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ReceiptReplayState,
    build_approved_live_audit_record,
    compute_approved_live_receipt_digest,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.stage_runtime import resolve_run_artifacts
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)


def file_hash(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class TestDuplicateProfileReview(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root, self.run = _prepare_fixture_run(temp.name)
        self.handoff = _write_handoff_jsonl(temp.name)
        self.resolved = resolve_run_artifacts(
            source_pack_root=self.root, run_id=RUN_ID, paper_id=PAPER_ID
        )
        self.index = self.run / "index/index-status.json"
        index = json.loads(self.index.read_bytes())
        self.scan = {
            "matched_existing": True,
            "paper_id": PAPER_ID,
            "source_hash": self.resolved.source_hash,
            "match_type": "same-PDF-different-profile",
        }
        index["duplicate_scan"] = self.scan
        self.index.write_bytes(canonical_json_bytes(index))
        current = self.resolved.source_pack_dir / "selected/fulltext.md"
        self.existing = self.run / "reports/duplicate-existing-text.md"
        self.existing.parent.mkdir(parents=True, exist_ok=True)
        self.existing.write_bytes(
            current.read_bytes() + b"\nHistorical fixture text profile.\n"
        )
        self.payload = {
            "schema_version": SCHEMA_VERSION,
            "paper_id": PAPER_ID,
            "run_id": RUN_ID,
            "source_hash": self.resolved.source_hash,
            "decision": "retain-separate-profile",
            "reviewer_id": "fixture-reviewer",
            "reason": "Preserve distinct historical text profile.",
            "duplicate_scan_sha256": review_digest(self.scan),
            "index_status_sha256": file_hash(self.index),
            "existing_profile": {
                "model": "fixture/old",
                "selected_text_ref": self.existing.relative_to(self.root).as_posix(),
                "selected_text_sha256": file_hash(self.existing),
                "source_hash": self.resolved.source_hash,
            },
            "current_profile": {
                "model": "fixture/new",
                "selected_text_ref": current.relative_to(self.root).as_posix(),
                "selected_text_sha256": file_hash(current),
                "source_hash": self.resolved.source_hash,
            },
        }

    def publish_review(self):
        request = duplicate_review_request(
            self.payload, run_dir=self.run, source_pack_root=self.root
        )
        body = {
            "schema_version": "millefeuille-approved-live-receipt/v0.1",
            "receipt_id": "fixture-duplicate-review-01",
            "run_id": RUN_ID,
            "approval": {
                "approver_id": "fixture-reviewer",
                "approved_at": "2025-01-01T00:00:00Z",
            },
            "expires_at": "2025-01-01T01:00:00Z",
            "scope": {
                "operations": list(request.operations),
                "targets": [t.to_dict() for t in request.targets],
                "max_items": 1,
                "selector": request.selector.to_dict(),
                "output_root": request.output_root,
                "source_pack_root": request.source_pack_root,
                "provider": None,
                "limits": {"max_provider_calls": 0, "max_cost_usd_micros": 0},
                "disposal_policy": request.disposal_policy.to_dict(),
                "stop_conditions": list(request.stop_conditions),
            },
        }
        body["integrity"] = {
            "algorithm": "sha256",
            "content_digest": compute_approved_live_receipt_digest(body),
        }
        receipt = ApprovedLiveReceipt.from_dict(body)
        audit = build_approved_live_audit_record(
            receipt,
            request,
            evaluated_at=datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
            status="consumed",
            replay_state=ReceiptReplayState(),
        )
        for ref, value in (
            (REVIEW_REF, self.payload),
            (RECEIPT_REF, body),
            (AUDIT_REF, audit),
        ):
            (self.run / ref).write_bytes(canonical_json_bytes(value))

    def summary(self):
        return _build_acceptance_summary(
            resolved=self.resolved, handoff_path=self.handoff, duplicate_scan_path=None
        ).to_dict()

    def test_unreviewed_match_remains_needs_review(self):
        value = self.summary()
        self.assertEqual(value["status"], "needs-review")
        self.assertTrue(value["duplicate_scan"]["matched_existing"])

    def test_full_acceptance_recognizes_review_and_keeps_original_match(self):
        self.publish_review()
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        value = self.summary()
        self.assertEqual(value["status"], "pass")
        self.assertTrue(value["duplicate_scan"]["matched_existing"])
        self.assertEqual(
            value["duplicate_scan"]["review"]["decision"], "retain-separate-profile"
        )
        self.assertEqual(value.get("review_reasons", []), [])
        self.assertEqual(
            before,
            {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()},
        )

    def test_historical_expired_receipt_is_evidence_without_replay(self):
        self.publish_review()
        a = (self.run / AUDIT_REF).read_bytes()
        self.assertIsNotNone(
            load_duplicate_profile_review(
                resolved=self.resolved, duplicate_scan=self.scan
            )
        )
        self.assertEqual(a, (self.run / AUDIT_REF).read_bytes())

    def test_identity_scan_index_and_review_changes_fail(self):
        self.publish_review()
        original = deepcopy(self.payload)
        for key in (
            "paper_id",
            "run_id",
            "source_hash",
            "duplicate_scan_sha256",
            "index_status_sha256",
            "decision",
            "reviewer_id",
            "reason",
        ):
            with self.subTest(key=key):
                changed = deepcopy(original)
                changed[key] = ""
                (self.run / REVIEW_REF).write_bytes(canonical_json_bytes(changed))
                with self.assertRaises(MillefeuilleContractError):
                    self.summary()
        (self.run / REVIEW_REF).write_bytes(canonical_json_bytes(original))
        self.index.write_bytes(self.index.read_bytes() + b" ")
        with self.assertRaises(MillefeuilleContractError):
            self.summary()

    def test_profile_and_receipt_audit_drift_fail(self):
        self.publish_review()
        old = self.existing.read_bytes()
        self.existing.write_bytes(old + b"drift")
        with self.assertRaises(MillefeuilleContractError):
            self.summary()
        self.existing.write_bytes(old)
        audit = json.loads((self.run / AUDIT_REF).read_bytes())
        audit["status"] = "validated"
        (self.run / AUDIT_REF).write_bytes(canonical_json_bytes(audit))
        with self.assertRaises(MillefeuilleContractError):
            self.summary()

    def test_same_text_different_model_remains_invalid(self):
        current = self.resolved.source_pack_dir / "selected/fulltext.md"
        self.existing.write_bytes(current.read_bytes())
        self.payload["existing_profile"]["selected_text_sha256"] = file_hash(
            self.existing
        )
        self.publish_review()
        with self.assertRaises(MillefeuilleContractError):
            self.summary()

    def test_broader_receipt_scope_cannot_resolve_review(self):
        self.publish_review()
        path = self.run / RECEIPT_REF
        body = json.loads(path.read_bytes())
        body["scope"]["max_items"] = 2
        body["integrity"]["content_digest"] = compute_approved_live_receipt_digest(body)
        path.write_bytes(canonical_json_bytes(body))
        with self.assertRaises(MillefeuilleContractError):
            self.summary()

    def test_escaping_or_wrong_current_ref_is_rejected(self):
        self.publish_review()
        for value in ("../../../../escape.md", "reports/duplicate-existing-text.md"):
            with self.subTest(value=value):
                changed = deepcopy(self.payload)
                changed["current_profile"]["selected_text_ref"] = value
                (self.run / REVIEW_REF).write_bytes(canonical_json_bytes(changed))
                with self.assertRaises(MillefeuilleContractError):
                    self.summary()

    def test_missing_or_unknown_review_evidence_cannot_pass(self):
        self.publish_review()
        audit = (self.run / AUDIT_REF).read_bytes()
        (self.run / AUDIT_REF).unlink()
        with self.assertRaises(MillefeuilleContractError):
            self.summary()
        (self.run / AUDIT_REF).write_bytes(audit)
        changed = deepcopy(self.payload)
        changed["grant_all_future_writes"] = True
        (self.run / REVIEW_REF).write_bytes(canonical_json_bytes(changed))
        with self.assertRaises(MillefeuilleContractError):
            self.summary()

    def test_review_symlink_fails_closed(self):
        self.publish_review()
        path = self.run / REVIEW_REF
        data = path.read_bytes()
        path.unlink()
        target = self.run / "reports/review-external.json"
        target.write_bytes(data)
        try:
            path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(MillefeuilleContractError):
            self.summary()


if __name__ == "__main__":
    unittest.main()
