"""Offline selection and fallback routing for summary executor work units."""

from __future__ import annotations

from io import StringIO
import json
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import build_summary_execution_plan
from millefeuille.domain.model_executor import (
    build_summary_work_unit_request,
    resolve_summary_model_route,
)


class TestSummaryModelRouting(unittest.TestCase):
    def test_profile_default_is_stable_and_no_call(self):
        route = resolve_summary_model_route(
            profile="research-default", stage="summarize_page"
        )
        self.assertEqual(route["profile_model"], "openai/gpt-5.6-sol")
        self.assertEqual(route["requested_model"], "openai/gpt-5.6-sol")
        self.assertEqual(route["thinking"], "xhigh")
        self.assertFalse(route["override_used"])
        self.assertEqual(route["fallback"], {"policy": "none", "models": []})
        self.assertEqual(route["retry"], {"max_attempts": 1, "retry_on": []})
        self.assertFalse(route["authentication"]["credential_lookup_performed"])
        self.assertFalse(route["provider_call_performed"])

    def test_override_and_fallback_match_work_unit_request(self):
        options = {
            "requested_model": "xai/grok-4.6",
            "thinking": "xhigh",
            "fallback_models": ["openai/gpt-5.6-sol"],
            "retry_on": ["model_unavailable", "timeout"],
        }
        route = resolve_summary_model_route(
            profile="research-default", stage="summarize_section", **options
        )
        request = build_summary_work_unit_request(
            execution_plan=build_summary_execution_plan(
                profile="research-default", stage="summarize_section"
            ),
            work_unit={"unit_id": "section-s1", "source_locators": ["p.1#s1"]},
            input_payload=b"transient input",
            output_schema_id="summary-section",
            output_schema_version="v1",
            **options,
        )
        self.assertTrue(route["override_used"])
        self.assertEqual(request["requested_model"], route["requested_model"])
        self.assertEqual(request["thinking"], route["thinking"])
        self.assertEqual(request["retry"], route["retry"])
        self.assertEqual(request["fallback"], route["fallback"])
        self.assertEqual(
            route,
            resolve_summary_model_route(
                profile="research-default", stage="summarize_section", **options
            ),
        )

    def test_unknown_models_and_hidden_fallback_fail_closed(self):
        base = {"profile": "research-default", "stage": "summarize_page"}
        invalid = (
            {"requested_model": "grok-4.6"},
            {"thinking": "high"},
            {"fallback_models": ["xai/grok-4.6"]},
            {"retry_on": ["timeout"]},
            {
                "fallback_models": ["openai/gpt-5.6-sol"],
                "retry_on": ["timeout"],
            },
            {
                "fallback_models": ["xai/grok-4.6", "xai/grok-4.6"],
                "retry_on": ["timeout"],
            },
            {
                "fallback_models": ["xai/grok-4.6"],
                "retry_on": ["timeout", "model_unavailable"],
            },
        )
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(
                MillefeuilleContractError
            ):
                resolve_summary_model_route(**base, **options)
        with self.assertRaisesRegex(MillefeuilleContractError, "fixture-only"):
            resolve_summary_model_route(
                profile="offline-preview", stage="summarize_page"
            )

    def test_cli_route_is_no_call_and_rejects_mixed_actions(self):
        out, err = StringIO(), StringIO()
        args = [
            "models", "--route", "--profile", "research-default",
            "--stage", "summarize_page", "--model", "xai/grok-4.6",
            "--fallback-model", "openai/gpt-5.6-sol",
            "--retry-on", "timeout", "--json",
        ]
        self.assertEqual(run_stage_cli(args, stdout=out, stderr=err), 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["requested_model"], "xai/grok-4.6")
        self.assertEqual(payload["fallback"]["models"], ["openai/gpt-5.6-sol"])
        self.assertFalse(payload["provider_call_performed"])

        out, err = StringIO(), StringIO()
        self.assertEqual(
            run_stage_cli(args + ["--plan"], stdout=out, stderr=err), 2
        )
        self.assertIn("cannot be combined", err.getvalue())


if __name__ == "__main__":
    unittest.main()
