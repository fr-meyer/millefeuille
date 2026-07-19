"""Tests for deterministic no-call model execution plans."""

from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import build_summary_execution_plan
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE


class ModelExecutionPlanTests(unittest.TestCase):
    def test_research_summary_plan_is_no_call_and_records_profile_controls(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )

        self.assertEqual(
            plan["schema_version"],
            "millefeuille-model-execution-plan/v0.1",
        )
        self.assertEqual(plan["requested_model"], "openai/gpt-5.6-sol")
        self.assertEqual(plan["provider"], "openai")
        self.assertEqual(
            plan["authentication"]["lane"],
            "openclaw-native-codex-oauth",
        )
        self.assertFalse(plan["authentication"]["credential_lookup_performed"])
        self.assertEqual(plan["fallback"], {"policy": "none", "model": None})
        self.assertEqual(plan["requested_parameters"]["reasoning_effort"], "xhigh")
        self.assertEqual(plan["requested_parameters"]["fast_mode"], "off")
        self.assertFalse(plan["execution"]["provider_call_permitted"])
        self.assertFalse(plan["execution"]["provider_call_performed"])
        self.assertFalse(plan["execution"]["ready_for_approved_live_execution"])
        self.assertEqual(len(plan["execution"]["blockers"]), 2)
        self.assertFalse(plan["provenance_contract"]["actual_values_recorded"])
        self.assertIn(
            "resolved_model",
            plan["provenance_contract"]["required_fields"],
        )

    def test_offline_preview_plan_is_fixture_only_and_has_no_live_blockers(self):
        plan = build_summary_execution_plan(
            profile="offline-preview",
            stage="summarize_page",
        )

        self.assertTrue(plan["execution"]["fixture_only"])
        self.assertEqual(plan["execution"]["blockers"], [])
        self.assertEqual(plan["authentication"]["lane"], "none")
        self.assertEqual(plan["provider"], "none")

    def test_plan_rejects_unknown_profile_and_non_summary_stage(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "unknown model profile"):
            build_summary_execution_plan(
                profile="missing",
                stage="summarize_page",
            )
        with self.assertRaisesRegex(MillefeuilleContractError, "must be one of"):
            build_summary_execution_plan(
                profile="research-default",
                stage="paper_card",
            )

    def test_plan_fails_closed_for_missing_auth_or_inconsistent_fallback(self):
        missing_auth = deepcopy(DEFAULT_MODEL_PROFILE_BUNDLE)
        del missing_auth["profiles"]["research-default"]["summarize_page"][
            "auth_lane"
        ]
        with self.assertRaisesRegex(MillefeuilleContractError, "auth_lane"):
            build_summary_execution_plan(
                profile="research-default",
                stage="summarize_page",
                bundle=missing_auth,
            )

        inconsistent_fallback = deepcopy(DEFAULT_MODEL_PROFILE_BUNDLE)
        inconsistent_fallback["profiles"]["research-default"]["summarize_page"][
            "fallback_model"
        ] = "openai/other-model"
        with self.assertRaisesRegex(MillefeuilleContractError, "must be absent"):
            build_summary_execution_plan(
                profile="research-default",
                stage="summarize_page",
                bundle=inconsistent_fallback,
            )

        mismatched_lane = deepcopy(DEFAULT_MODEL_PROFILE_BUNDLE)
        mismatched_lane["profiles"]["research-default"]["summarize_page"][
            "backend"
        ] = "fixture"
        with self.assertRaisesRegex(MillefeuilleContractError, "fixture execution"):
            build_summary_execution_plan(
                profile="research-default",
                stage="summarize_page",
                bundle=mismatched_lane,
            )

    def test_models_cli_builds_plan_and_requires_explicit_pair(self):
        stdout = StringIO()
        exit_code = run_stage_cli(
            [
                "models",
                "--plan",
                "--profile",
                "research-default",
                "--stage",
                "summarize_section",
                "--json",
            ],
            stdout=stdout,
        )
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["stage"], "summarize_section")
        self.assertEqual(payload["status"], "planned-offline")

        stderr = StringIO()
        incomplete_exit = run_stage_cli(
            ["models", "--plan", "--profile", "research-default", "--json"],
            stderr=stderr,
        )
        self.assertEqual(incomplete_exit, 2)
        self.assertIn("requires --profile and --stage", stderr.getvalue())

    def test_execution_plan_schema_matches_no_call_boundary(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "specs"
            / "millefeuille-pipeline"
            / "model-execution-plan.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            "millefeuille-model-execution-plan/v0.1",
        )
        self.assertFalse(schema["additionalProperties"])
        expected_nested_required = {
            "requested_parameters": {"record_usage"},
            "authentication": {"lane", "credential_lookup_performed"},
            "fallback": {"policy", "model"},
            "execution": {
                "provider_call_permitted",
                "provider_call_performed",
                "fixture_only",
                "ready_for_approved_live_execution",
                "blockers",
            },
            "provenance_contract": {
                "schema_version",
                "required_fields",
                "actual_values_recorded",
            },
        }
        for field_name, required_fields in expected_nested_required.items():
            with self.subTest(field_name=field_name):
                field_schema = schema["properties"][field_name]
                self.assertFalse(field_schema["additionalProperties"])
                self.assertEqual(set(field_schema["required"]), required_fields)

        generated = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        _assert_required_shape(generated, schema)
        malformed = deepcopy(generated)
        del malformed["authentication"]["lane"]
        with self.assertRaisesRegex(AssertionError, "authentication.lane"):
            _assert_required_shape(malformed, schema)

        execution = schema["properties"]["execution"]["properties"]
        self.assertFalse(execution["provider_call_permitted"]["const"])
        self.assertFalse(execution["provider_call_performed"]["const"])
        self.assertFalse(execution["ready_for_approved_live_execution"]["const"])


def _assert_required_shape(
    payload: dict[str, object],
    schema: dict[str, object],
) -> None:
    for field_name in schema["required"]:
        if field_name not in payload:
            raise AssertionError(field_name)
        field_schema = schema["properties"].get(field_name, {})
        nested_required = field_schema.get("required", [])
        for nested_name in nested_required:
            if nested_name not in payload[field_name]:
                raise AssertionError(f"{field_name}.{nested_name}")


if __name__ == "__main__":
    unittest.main()
