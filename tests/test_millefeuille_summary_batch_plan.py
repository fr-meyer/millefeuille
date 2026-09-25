"""Integration checks for no-call grounded GPT batch handoff."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from tests import test_millefeuille_summary_dispatch as dispatch_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestGroundedSummaryBatchPlan(unittest.TestCase):
    def _plan(self, root: Path, *, no_sections: bool = False):
        source = (
            "# Page 1\nPrivate text.\n"
            if no_sections
            else ("# Page 1\n# Introduction\nPrivate text.\n")
        )
        route, structure, preparation = dispatch_tests.TestSummaryDispatch()._fixture(
            root, markdown_text=source
        )
        evidence = {
            "route_evidence_path": route,
            "structure_evidence_path": structure,
            "preparation_path": preparation,
        }
        return evidence

    def test_plans_versioned_prompts_and_manifest_without_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            evidence = self._plan(root)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            plan = plan_grounded_gpt_summary_batch(**evidence)
            again = plan_grounded_gpt_summary_batch(**evidence)
            after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

            self.assertEqual(before, after)
            self.assertEqual(plan.manifest.sha256, again.manifest.sha256)
            self.assertEqual(len(plan.batch.units), 3)
            self.assertNotIn("Private text", repr(plan))
            self.assertNotIn(b"Private text", plan.manifest.json_bytes)
            for unit in plan.batch.units:
                self.assertIn(b"Millefeuille prompt summary-", unit.input_payload)
            schema = json.loads(
                (
                    Path(__file__).parents[1]
                    / "specs/millefeuille-pipeline"
                    / "summary-execution-manifest.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(json.loads(plan.manifest.json_bytes))

    def test_zero_section_stage_and_source_drift(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            evidence = self._plan(root, no_sections=True)
            plan = plan_grounded_gpt_summary_batch(**evidence)
            self.assertEqual(
                [unit.stage for unit in plan.batch.units],
                ["summarize_page", "summarize_full_paper"],
            )
            self.assertEqual(json.loads(plan.manifest.json_bytes)["work_unit_count"], 2)

            (root / "selected.md").write_text("Changed text.\n", encoding="utf-8")
            with self.assertRaises(MillefeuilleContractError):
                plan_grounded_gpt_summary_batch(**evidence)
