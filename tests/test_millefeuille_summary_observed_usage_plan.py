"""No-write observed usage plan for accepted GPT summary batches."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_observed_usage_plan import (
    plan_gpt_summary_observed_usage,
)
from millefeuille.domain.summary_results import accept_summary_execution_batch
from tests import test_millefeuille_summary_output_plan as output_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGptSummaryObservedUsagePlan(unittest.TestCase):
    def setUp(self):
        fixture = output_tests.TestGptSummaryOutputPlan(
            "test_maps_all_units_without_writes_or_text_in_repr"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture

    def _plan(self, outcome=None):
        return plan_gpt_summary_observed_usage(
            outcome=self.fixture.outcome if outcome is None else outcome,
            run_id="run-gpt-1",
            route_evidence_path=self.fixture.evidence["route_evidence_path"],
            structure_evidence_path=self.fixture.evidence["structure_evidence_path"],
            preparation_path=self.fixture.evidence["preparation_path"],
        )

    def _with_usage(self, usage=None):
        actual = usage or {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        }
        executions = tuple(
            OpenClawModelExecution(
                result={**execution.result, "usage": actual},
                output=execution.output,
            )
            for execution in self.fixture.outcome.executions
        )
        accepted = accept_summary_execution_batch(
            batch=self.fixture.outcome.batch,
            executions={
                (unit.stage, unit.unit_id): execution
                for unit, execution in zip(
                    self.fixture.outcome.batch.units, executions, strict=True
                )
            },
            route_evidence_path=self.fixture.evidence["route_evidence_path"],
            structure_evidence_path=self.fixture.evidence["structure_evidence_path"],
            preparation_path=self.fixture.evidence["preparation_path"],
        )
        return replace(self.fixture.outcome, executions=executions, accepted=accepted)

    def test_observed_usage_is_bound_without_writes_or_private_text(self):
        root = self.fixture.root
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        outcome = self._with_usage()
        planned = self._plan(outcome)
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(planned, self._plan(outcome))
        self.assertEqual(planned.unit_count, 3)
        self.assertEqual(
            planned.observed_usage_sha256,
            "sha256:" + hashlib.sha256(planned.observed_usage_json).hexdigest(),
        )
        manifest = json.loads(planned.observed_usage_json)
        self.assertEqual(manifest["observed_usage_ref"], planned.observed_usage_ref)
        self.assertEqual(
            manifest["write_manifest_sha256"], planned.write_manifest_sha256
        )
        self.assertEqual(manifest["entries"][0]["usage"]["total_tokens"], 120)
        self.assertNotIn(b"Private accepted summary", planned.observed_usage_json)
        self.assertNotIn("Private accepted summary", repr(planned))

    def test_missing_or_inconsistent_usage_fails_closed(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "unavailable"):
            self._plan()
        bad = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 121}
        with self.assertRaises(MillefeuilleContractError):
            self._with_usage(bad)

    def test_source_drift_is_rejected(self):
        outcome = self._with_usage()
        (self.fixture.root / "selected.md").write_text(
            "Changed source.\n", encoding="utf-8"
        )
        with self.assertRaises(MillefeuilleContractError):
            self._plan(outcome)
