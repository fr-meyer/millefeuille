"""Card output is accepted only with exact source, result, usage and byte bindings."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import materialize_model_executor_result
from millefeuille.domain.paper_card_inputs import plan_published_gpt_paper_card_request
from millefeuille.domain.paper_card_results import (
    validate_published_gpt_paper_card_execution,
)
from tests import test_millefeuille_summary_published_handoff as handoff_tests
from tests.platform_capabilities import requires_secure_nofollow_writes

SPEC_DIR = Path(__file__).parents[1] / "specs/millefeuille-pipeline"


@requires_secure_nofollow_writes
class TestGptPaperCardResults(unittest.TestCase):
    def setUp(self):
        fixture = handoff_tests.TestPublishedSummaryHandoff(
            "test_handoff_matches_source_pack_and_has_no_effects_or_private_text"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.values = dict(
            source_pack_root=fixture.fixture.root,
            publication=fixture.publication,
            **fixture.evidence,
        )
        self.plan = plan_published_gpt_paper_card_request(
            **self.values, run_id="run-card-02"
        )
        self.content = {
            "schema_version": "v1",
            "paper_id": self.plan.paper_id,
            "run_id": self.plan.run_id,
            "source_hash": self.plan.source_hash,
            "source_locators": list(self.plan.source_locators),
            **dict.fromkeys(
                (
                    "one_line_thesis",
                    "primary_contribution",
                    "problem_addressed",
                    "method_or_approach",
                    "data_modality_domain",
                    "main_results",
                    "limitations",
                ),
                "Private reported card content.",
            ),
            "classification_clues": ["reported concept"],
            "quality_warnings": [],
        }
        self.output = json.dumps(self.content).encode()

    def _execution(self):
        profile = "openclaw:franck:openai-oauth"
        result = materialize_model_executor_result(
            request=self.plan.request,
            status="succeeded",
            actual_model="openai/gpt-5.6-sol",
            auth_profile_ref=profile,
            started_at="2026-09-26T01:00:00Z",
            completed_at="2026-09-26T01:00:02Z",
            attempts=[
                {
                    "sequence": 1,
                    "model": "openai/gpt-5.6-sol",
                    "actual_model": "openai/gpt-5.6-sol",
                    "status": "succeeded",
                    "started_at": "2026-09-26T01:00:00Z",
                    "completed_at": "2026-09-26T01:00:02Z",
                    "auth_class": "subscription_oauth",
                    "auth_profile_ref": profile,
                    "failure_code": None,
                }
            ],
            fallback_reason=None,
            schema_validation_status="passed",
            output_sha256="sha256:" + hashlib.sha256(self.output).hexdigest(),
            output_bytes=len(self.output),
            usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            cost_micro_usd=0,
        )
        return OpenClawModelExecution(result=result, output=self.output)

    def _validate(self, *, execution=None, plan=None):
        return validate_published_gpt_paper_card_execution(
            **self.values,
            plan=plan or self.plan,
            execution=execution or self._execution(),
        )

    def test_validates_exact_result_and_plans_actual_metadata_without_writes(self):
        root = self.fixture.fixture.root
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        validated = self._validate()
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(validated.executor_result["usage"]["total_tokens"], 120)
        self.assertNotIn("Private", repr(validated))
        self.assertNotIn(b"Private", validated.provenance_json)
        provenance = json.loads(validated.provenance_json)
        schema = json.loads(
            (SPEC_DIR / "paper-card-provenance.schema.json").read_text()
        )
        resources = []
        for name in ("model-executor-request", "model-executor-result"):
            referenced = json.loads((SPEC_DIR / f"{name}.schema.json").read_text())
            resources.append((referenced["$id"], Resource.from_contents(referenced)))
        Draft202012Validator(schema).check_schema(schema)
        Draft202012Validator(
            schema, registry=Registry().with_resources(resources)
        ).validate(provenance)
        self.assertEqual(provenance["request_plan_sha256"], self.plan.manifest_sha256)
        self.assertEqual(provenance["result"], validated.executor_result)
        self.assertFalse(provenance["secret_material_persisted"])
        self.assertFalse(provenance["paper_text_persisted"])
        self.assertEqual(
            (validated.provider_calls_performed, validated.writes_performed), (0, 0)
        )

    def test_rejects_missing_usage_cost_and_nonzero_cost(self):
        for key, value in (
            ("usage", None),
            ("cost", None),
            ("cost", {"currency": "USD", "micro_usd": 1}),
        ):
            execution = self._execution()
            result = deepcopy(execution.result)
            result[key] = value
            with (
                self.subTest(key=key, value=value),
                self.assertRaises(MillefeuilleContractError),
            ):
                self._validate(
                    execution=OpenClawModelExecution(result, execution.output)
                )

    def test_rejects_model_auth_usage_schema_and_request_drift(self):
        changes = (
            ("actual_model", "xai/grok-4.6"),
            ("authentication", {"class": "api_key", "profile_ref": "injected"}),
            ("thinking", "high"),
            ("usage", {"input_tokens": 100, "output_tokens": 20, "total_tokens": 999}),
            (
                "schema_validation",
                {
                    "status": "failed",
                    "schema_id": "millefeuille-paper-card-content",
                    "schema_version": "v1",
                },
            ),
            ("request_sha256", "sha256:" + "0" * 64),
        )
        for key, value in changes:
            execution = self._execution()
            result = deepcopy(execution.result)
            result[key] = value
            with self.subTest(key=key), self.assertRaises(MillefeuilleContractError):
                self._validate(
                    execution=OpenClawModelExecution(result, execution.output)
                )

    def test_rejects_output_bytes_and_content_identity_drift(self):
        execution = self._execution()
        with self.assertRaisesRegex(MillefeuilleContractError, "output binding"):
            self._validate(
                execution=OpenClawModelExecution(execution.result, b"changed")
            )
        self.content["paper_id"] = "other-paper"
        self.output = json.dumps(self.content).encode()
        with self.assertRaisesRegex(MillefeuilleContractError, "identity drift"):
            self._validate()

    def test_rejects_changed_publication_and_mutated_plans(self):
        plan = replace(self.plan, manifest_sha256="sha256:" + "0" * 64)
        with self.assertRaisesRegex(MillefeuilleContractError, "plan drift"):
            self._validate(plan=plan)
        path = self.fixture.fixture.root / self.fixture.fixture.plan.texts[0].ref
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(MillefeuilleContractError):
            self._validate()
