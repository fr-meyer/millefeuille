"""Exact, private-text-free planning for GPT summary batch approval."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_dispatch import (
    SummaryDispatchBatch,
    plan_verified_summary_dispatch,
)
from millefeuille.domain.summary_execution_manifest import (
    plan_summary_execution_manifest,
)
from tests import test_millefeuille_summary_dispatch as dispatch_tests
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestSummaryExecutionManifest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        route, structure, preparation = dispatch_tests.TestSummaryDispatch()._fixture(
            self.root
        )
        self.evidence = {
            "route_evidence_path": route,
            "structure_evidence_path": structure,
            "preparation_path": preparation,
        }
        self.batch = plan_verified_summary_dispatch(
            **self.evidence,
            prompt_builder=dispatch_tests.TestSummaryDispatch._prompt,
            output_contracts=dispatch_tests.CONTRACTS,
        )

    def test_plans_exact_canonical_manifest_without_private_text_or_writes(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        manifest = plan_summary_execution_manifest(batch=self.batch, **self.evidence)
        again = plan_summary_execution_manifest(batch=self.batch, **self.evidence)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

        self.assertEqual(before, after)
        self.assertEqual(manifest, again)
        self.assertEqual(
            manifest.sha256,
            "sha256:" + hashlib.sha256(manifest.json_bytes).hexdigest(),
        )
        self.assertNotIn(b"Private text", manifest.json_bytes)
        self.assertNotIn(b"p.1#s1", manifest.json_bytes)
        self.assertNotIn(b"Private text", repr(manifest).encode())
        payload = json.loads(manifest.json_bytes)
        self.assertEqual(payload["work_unit_count"], 3)
        self.assertEqual(payload["provider_calls_performed"], 0)
        self.assertEqual(
            [(unit["stage"], unit["unit_id"]) for unit in payload["units"]],
            [
                ("summarize_page", "page-1"),
                ("summarize_section", "section-s1"),
                ("summarize_full_paper", "full-paper"),
            ],
        )
        schema = json.loads(
            (
                Path(__file__).parents[1]
                / "specs/millefeuille-pipeline/summary-execution-manifest.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)

    def test_prompt_change_changes_fingerprint(self):
        first = plan_summary_execution_manifest(batch=self.batch, **self.evidence)
        changed_batch = plan_verified_summary_dispatch(
            **self.evidence,
            prompt_builder=lambda *args: (
                dispatch_tests.TestSummaryDispatch._prompt(*args)
                + b"New approved prompt version input."
            ),
            output_contracts=dispatch_tests.CONTRACTS,
        )
        changed = plan_summary_execution_manifest(batch=changed_batch, **self.evidence)
        self.assertNotEqual(first.sha256, changed.sha256)

    def test_rejects_partial_or_tampered_batch_and_changed_preparation(self):
        partial = SummaryDispatchBatch(
            paper_id=self.batch.paper_id,
            preparation_sha256=self.batch.preparation_sha256,
            units=self.batch.units[:-1],
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "stage coverage drift"):
            plan_summary_execution_manifest(batch=partial, **self.evidence)

        tampered = deepcopy(self.batch)
        tampered.units[0].request["requested_model"] = "xai/grok-4.6"
        with self.assertRaises(MillefeuilleContractError):
            plan_summary_execution_manifest(batch=tampered, **self.evidence)

        self.evidence["preparation_path"].write_text("{}", encoding="utf-8")
        with self.assertRaises(MillefeuilleContractError):
            plan_summary_execution_manifest(batch=self.batch, **self.evidence)
