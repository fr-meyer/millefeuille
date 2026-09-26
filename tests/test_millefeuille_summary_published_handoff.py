"""Offline checks for immutable published-summary input handoff."""

from dataclasses import replace
import json
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import compute_operator_root_target_id
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    build_source_pack_manifest,
)
from millefeuille.domain.summary_preparation import verify_summary_preparation_package
from millefeuille.domain.summary_publication_bundle import (
    plan_gpt_summary_publication_bundle,
)
from millefeuille.domain.summary_published_handoff import (
    GptSummaryPublicationIdentity,
    plan_published_gpt_summary_handoff,
)
from tests import test_millefeuille_summary_output_write_scope as scope_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestPublishedSummaryHandoff(unittest.TestCase):
    def setUp(self):
        fixture = scope_tests.TestGptSummaryOutputWriteScope(
            "test_exact_write_scope_is_previewed_without_effects"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.evidence = {
            key: fixture.evidence[key]
            for key in (
                "route_evidence_path",
                "structure_evidence_path",
                "preparation_path",
            )
        }
        self.bundle = plan_gpt_summary_publication_bundle(
            outcome=fixture.outcome,
            run_id=fixture.run_id,
            source_pack_root=fixture.root,
            packet=fixture.packet,
            receipt=fixture.receipt,
            **self.evidence,
        )
        for item in self.bundle.files:
            target = fixture.root / item.ref
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.data)
        prepared = verify_summary_preparation_package(**self.evidence)
        identity = prepared["identity"]
        source = build_source_pack_manifest(
            evidence=RecoveredPdfEvidence(
                item_key=identity["item_key"],
                attachment_key=identity["attachment_key"],
                canonical_filename=identity["canonical_filename"],
                recovered_pdf_path=fixture.root / "absent-recovered.pdf",
                expected_sha256=identity["expected_sha256"],
            ),
            paper_id=fixture.plan.paper_id,
            source_hash="sha256:" + identity["expected_sha256"],
            byte_size=100,
            created_at="2026-09-26T00:00:00Z",
        )
        self.source_path = (
            fixture.root / "zotero" / fixture.plan.paper_id / "manifest.json"
        )
        self.source_path.parent.mkdir(parents=True)
        self.source_path.write_text(json.dumps(source))
        self.publication = GptSummaryPublicationIdentity(
            paper_id=fixture.plan.paper_id,
            run_id=fixture.run_id,
            root_target_id=compute_operator_root_target_id(str(fixture.root)),
            packet_digest=fixture.packet.content_digest,
            receipt_digest=fixture.receipt.content_digest,
            source_manifest_sha256=fixture.plan.source_manifest_sha256,
            write_manifest_sha256=fixture.plan.write_manifest_sha256,
            observed_usage_sha256=fixture.observed.observed_usage_sha256,
            provenance_manifest_sha256=fixture.provenance.provenance_manifest_sha256,
            bundle_manifest_sha256=self.bundle.bundle_manifest_sha256,
            file_count=len(self.bundle.files),
            total_bytes=self.bundle.total_bytes,
        )

    def _plan(self, **overrides):
        values = dict(
            source_pack_root=self.fixture.root,
            publication=self.publication,
            **self.evidence,
        )
        values.update(overrides)
        return plan_published_gpt_summary_handoff(**values)

    def test_handoff_matches_source_pack_and_has_no_effects_or_private_text(self):
        root = self.fixture.root
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        handoff = self._plan()
        self.assertEqual(handoff, self._plan())
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(handoff.provider_calls_performed, 0)
        self.assertEqual(handoff.writes_performed, 0)
        self.assertEqual(
            len(handoff.summary_text_refs), len(self.fixture.outcome.batch.units)
        )
        self.assertEqual(len(handoff.provenance_refs), len(handoff.summary_text_refs))
        self.assertEqual(len(handoff.source_input_refs), 2)
        self.assertNotIn("Private accepted summary", repr(handoff))

    def test_changed_text_snapshot_usage_and_provenance_are_rejected(self):
        targets = [
            self.fixture.plan.texts[0].ref,
            self.fixture.plan.source_inputs[0].ref,
            self.fixture.observed.observed_usage_ref,
            self.fixture.provenance.records[0].ref,
        ]
        for ref in targets:
            with self.subTest(ref=ref):
                path = self.fixture.root / ref
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaisesRegex(MillefeuilleContractError, "hash drift"):
                    self._plan()
                path.write_bytes(original)

    def test_missing_extra_and_symlink_artifacts_are_rejected(self):
        path = self.fixture.root / self.fixture.plan.texts[0].ref
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(MillefeuilleContractError):
            self._plan()
        path.write_bytes(original)
        extra = path.parent / "unapproved.md"
        extra.write_text("unapproved")
        with self.assertRaisesRegex(MillefeuilleContractError, "file coverage"):
            self._plan()
        extra.unlink()
        extra.symlink_to(self.source_path)
        with self.assertRaisesRegex(MillefeuilleContractError, "symlink"):
            self._plan()

    def test_wrong_source_pack_and_changed_preparation_are_rejected(self):
        source = json.loads(self.source_path.read_bytes())
        source["identity"]["zotero_attachment_key"] = "OTHER"
        self.source_path.write_text(json.dumps(source))
        with self.assertRaisesRegex(MillefeuilleContractError, "source-pack identity"):
            self._plan()
        source["identity"]["zotero_attachment_key"] = (
            verify_summary_preparation_package(**self.evidence)["identity"][
                "attachment_key"
            ]
        )
        self.source_path.write_text(json.dumps(source))
        (self.fixture.root / "selected.md").write_text("changed source")
        with self.assertRaises(MillefeuilleContractError):
            self._plan()

    def test_wrong_commit_metadata_is_rejected_without_live_authorization(self):
        for key, value in (
            ("bundle_manifest_sha256", "sha256:" + "0" * 64),
            ("packet_digest", "sha256:" + "0" * 64),
            ("receipt_digest", "sha256:" + "0" * 64),
            ("file_count", self.publication.file_count + 1),
            ("total_bytes", self.publication.total_bytes + 1),
            ("root_target_id", "sha256:" + "0" * 64),
            ("run_id", "../unsafe"),
        ):
            with self.subTest(key=key), self.assertRaises(MillefeuilleContractError):
                self._plan(publication=replace(self.publication, **{key: value}))
