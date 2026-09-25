"""Byte-exact no-write GPT publication bundle checks."""

from __future__ import annotations

import hashlib
import json
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_publication_bundle import (
    plan_gpt_summary_publication_bundle,
)
from tests import test_millefeuille_summary_output_write_scope as scope_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGptSummaryPublicationBundle(unittest.TestCase):
    def setUp(self):
        fixture = scope_tests.TestGptSummaryOutputWriteScope(
            "test_exact_write_scope_is_previewed_without_effects"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture

    def _plan(self, **overrides):
        f = self.fixture
        values = {
            "outcome": f.outcome,
            "run_id": f.run_id,
            "route_evidence_path": f.evidence["route_evidence_path"],
            "structure_evidence_path": f.evidence["structure_evidence_path"],
            "preparation_path": f.evidence["preparation_path"],
            "source_pack_root": f.root,
            "packet": f.packet,
            "receipt": f.receipt,
        }
        values.update(overrides)
        return plan_gpt_summary_publication_bundle(**values)

    def test_bundle_covers_every_approved_file_without_writes(self):
        root = self.fixture.root
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        bundle = self._plan()
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(bundle, self._plan())
        self.assertEqual(
            tuple(item.ref for item in bundle.files), bundle.preview.file_refs
        )
        self.assertEqual(
            bundle.total_bytes, sum(len(item.data) for item in bundle.files)
        )
        self.assertEqual(
            bundle.bundle_manifest_sha256,
            "sha256:" + hashlib.sha256(bundle.bundle_manifest_json).hexdigest(),
        )
        manifest = json.loads(bundle.bundle_manifest_json)
        self.assertEqual(len(manifest["entries"]), len(bundle.files))
        self.assertEqual(
            manifest["provenance_manifest_sha256"],
            bundle.preview.provenance_manifest_sha256,
        )
        for item, entry in zip(bundle.files, manifest["entries"], strict=True):
            self.assertEqual(item.ref, entry["ref"])
            self.assertEqual(
                item.sha256, "sha256:" + hashlib.sha256(item.data).hexdigest()
            )
            self.assertEqual(entry["sha256"], item.sha256)
            self.assertEqual(entry["bytes"], len(item.data))
        self.assertNotIn("Private accepted summary", repr(bundle))
        self.assertNotIn(b"Private accepted summary", bundle.bundle_manifest_json)

    def test_source_drift_or_wrong_packet_is_rejected(self):
        with self.assertRaises(MillefeuilleContractError):
            self._plan(packet=self.fixture.receipt)
        (self.fixture.root / "selected.md").write_text(
            "Changed source.\n", encoding="utf-8"
        )
        with self.assertRaises(MillefeuilleContractError):
            self._plan()
