"""No-write mapping from accepted GPT results to summary artifact plans."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_output_plan import plan_gpt_summary_outputs
from millefeuille.domain.summary_results import accept_summary_execution_batch
from tests import test_millefeuille_summary_results as result_tests
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_summary_trusted_approval import _case


@requires_secure_nofollow_writes
class TestGptSummaryOutputPlan(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "paper"
        self.root.mkdir()
        self.evidence, packet, receipt, _record = _case(self.root)
        self.plan = plan_grounded_gpt_summary_batch(
            route_evidence_path=self.evidence["route_evidence_path"],
            structure_evidence_path=self.evidence["structure_evidence_path"],
            preparation_path=self.evidence["preparation_path"],
        )
        preview = validate_gpt_summary_approval_preview(
            **self.evidence, packet=packet, receipt=receipt
        )
        executions = tuple(
            result_tests.TestSummaryResults._execution(
                unit, result_tests.TestSummaryResults._output(unit)
            )
            for unit in self.plan.batch.units
        )
        accepted = accept_summary_execution_batch(
            batch=self.plan.batch,
            executions={
                (unit.stage, unit.unit_id): execution
                for unit, execution in zip(
                    self.plan.batch.units, executions, strict=True
                )
            },
            route_evidence_path=self.evidence["route_evidence_path"],
            structure_evidence_path=self.evidence["structure_evidence_path"],
            preparation_path=self.evidence["preparation_path"],
        )
        self.outcome = TrustedGptSummaryOutcome(
            approval=preview,
            accepted=accepted,
            batch=self.plan.batch,
            executions=executions,
        )

    def _plan(self, outcome=None, run_id="run-gpt-1"):
        return plan_gpt_summary_outputs(
            outcome=self.outcome if outcome is None else outcome,
            run_id=run_id,
            route_evidence_path=self.evidence["route_evidence_path"],
            structure_evidence_path=self.evidence["structure_evidence_path"],
            preparation_path=self.evidence["preparation_path"],
        )

    def test_maps_all_units_without_writes_or_text_in_repr(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        planned = self._plan()
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(planned, self._plan())
        self.assertEqual(len(planned.texts), 3)
        self.assertTrue(
            planned.summary_record_ref.startswith("analyses/millefeuille/run-gpt-1/")
        )
        self.assertEqual(
            planned.write_manifest_ref,
            "analyses/millefeuille/run-gpt-1/summaries/write-manifest.json",
        )
        self.assertEqual(
            json.loads(planned.write_manifest_json)["write_manifest_ref"],
            planned.write_manifest_ref,
        )
        record = json.loads(planned.summary_record_json)
        self.assertEqual(
            [entry["grain"] for entry in record["summaries"]],
            ["page", "section", "full-paper"],
        )
        self.assertEqual(record["summaries"][-1]["scope"], "classification")
        self.assertEqual(
            planned.texts[0].sha256,
            "sha256:" + hashlib.sha256(planned.texts[0].data).hexdigest(),
        )
        self.assertNotIn("Private accepted summary", repr(planned))
        self.assertNotIn(b"Private accepted summary", planned.write_manifest_json)
        self.assertEqual(
            planned.write_manifest_sha256,
            "sha256:" + hashlib.sha256(planned.write_manifest_json).hexdigest(),
        )

    def test_summary_record_bytes_cannot_drift_from_write_fingerprint(self):
        planned = self._plan()
        original = planned.summary_record_json
        manifest = json.loads(planned.write_manifest_json)
        record = json.loads(original)
        record["summaries"][0]["text_ref"] = "texts/changed.md"
        self.assertEqual(planned.summary_record_json, original)
        self.assertNotEqual(record, json.loads(original))
        self.assertEqual(
            manifest["summary_record_sha256"],
            "sha256:" + hashlib.sha256(planned.summary_record_json).hexdigest(),
        )
        with self.assertRaises(FrozenInstanceError):
            planned.summary_record_json = b"{}\n"

    def test_different_runs_have_disjoint_output_refs(self):
        first = self._plan(run_id="run-gpt-1")
        second = self._plan(run_id="run-gpt-2")
        self.assertNotEqual(first.summary_record_ref, second.summary_record_ref)
        self.assertTrue(
            {item.ref for item in first.texts}.isdisjoint(
                {item.ref for item in second.texts}
            )
        )

    def test_rejects_unsafe_run_id_and_changed_source(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "run id"):
            self._plan(run_id="../escape")
        (self.root / "selected.md").write_text("Changed text.\n", encoding="utf-8")
        with self.assertRaises(MillefeuilleContractError):
            self._plan()

    def test_rejects_missing_execution_or_tampered_accepted_text(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "source or scope drift"):
            self._plan(replace(self.outcome, executions=self.outcome.executions[:-1]))
        unit = replace(self.outcome.accepted.units[0], summary="Fabricated text")
        accepted = replace(
            self.outcome.accepted,
            units=(unit, *self.outcome.accepted.units[1:]),
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "accepted output drift"):
            self._plan(replace(self.outcome, accepted=accepted))
