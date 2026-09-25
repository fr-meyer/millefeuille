"""No-write GPT provenance from observed results and run-scoped source refs."""

from __future__ import annotations

import hashlib
import json
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import validate_model_provenance_record
from millefeuille.domain.summary_model_provenance_plan import (
    plan_gpt_summary_model_provenance,
)
from millefeuille.domain.summary_output_plan import plan_gpt_summary_outputs
from tests import test_millefeuille_summary_observed_usage_plan as observed_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGptSummaryModelProvenancePlan(unittest.TestCase):
    def setUp(self):
        fixture = observed_tests.TestGptSummaryObservedUsagePlan(
            "test_observed_usage_is_bound_without_writes_or_private_text"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture

    def _plan(self, outcome=None):
        evidence = self.fixture.fixture.evidence
        return plan_gpt_summary_model_provenance(
            outcome=self.fixture._with_usage() if outcome is None else outcome,
            run_id="run-gpt-1",
            route_evidence_path=evidence["route_evidence_path"],
            structure_evidence_path=evidence["structure_evidence_path"],
            preparation_path=evidence["preparation_path"],
        )

    def test_strict_records_are_bound_to_real_source_and_output_refs(self):
        outcome = self.fixture._with_usage()
        root = self.fixture.fixture.root
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        planned = self._plan(outcome)
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(planned, self._plan(outcome))
        self.assertEqual(len(planned.records), 3)
        evidence = self.fixture.fixture.evidence
        outputs = plan_gpt_summary_outputs(
            outcome=outcome,
            run_id="run-gpt-1",
            route_evidence_path=evidence["route_evidence_path"],
            structure_evidence_path=evidence["structure_evidence_path"],
            preparation_path=evidence["preparation_path"],
        )
        self.assertEqual(planned.write_manifest_sha256, outputs.write_manifest_sha256)
        manifest = json.loads(planned.provenance_manifest_json)
        self.assertEqual(
            manifest["provenance_manifest_ref"], outputs.provenance_manifest_ref
        )
        self.assertEqual(
            planned.provenance_manifest_sha256,
            "sha256:" + hashlib.sha256(planned.provenance_manifest_json).hexdigest(),
        )
        for record, entry, text in zip(
            planned.records, manifest["entries"], outputs.texts, strict=True
        ):
            payload = json.loads(record.data)
            self.assertEqual(validate_model_provenance_record(payload), payload)
            self.assertEqual(payload["usage"]["total_tokens"], 120)
            self.assertEqual(
                payload["input_refs"],
                ["structure/inputs/selected.md", "structure/inputs/structure.json"],
            )
            self.assertEqual(
                payload["output_refs"],
                [text.ref.removeprefix("analyses/millefeuille/run-gpt-1/")],
            )
            self.assertEqual(entry["provenance_ref"], record.ref)
            self.assertEqual(entry["provenance_sha256"], record.sha256)
            self.assertEqual(
                record.sha256, "sha256:" + hashlib.sha256(record.data).hexdigest()
            )
        self.assertNotIn("Private accepted summary", repr(planned))
        self.assertNotIn(b"Private accepted summary", planned.provenance_manifest_json)

    def test_missing_usage_and_changed_source_fail_closed(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "unavailable"):
            self._plan(self.fixture.fixture.outcome)
        outcome = self.fixture._with_usage()
        (self.fixture.fixture.root / "selected.md").write_text(
            "Changed source.\n", encoding="utf-8"
        )
        with self.assertRaises(MillefeuilleContractError):
            self._plan(outcome)
