"""Offline Astra migration checks, no credentials or provider calls."""

from copy import deepcopy
import unittest

from millefeuille.clients.openclaw_model_client import _validate_openclaw_response
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import validate_model_executor_request
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from tests import test_millefeuille_summary_stage_selection as selection_tests
from tests.test_millefeuille_openclaw_model_client import _model_response
from tests.test_millefeuille_summary_execution_scope import _approved_pair


class TestAstraMigration(unittest.TestCase):
    def test_all_gpt_profiles_and_unchanged_non_gpt_routes(self):
        profiles = DEFAULT_MODEL_PROFILE_BUNDLE["profiles"]["research-default"]
        for stage in (
            "summarize_page",
            "summarize_section",
            "summarize_full_paper",
            "paper_card",
            "classify",
        ):
            with self.subTest(stage=stage):
                self.assertEqual(profiles[stage]["model"], "openai/gpt-6-astra")
                self.assertEqual(profiles[stage]["reasoning_effort"], "xhigh")
                self.assertEqual(profiles[stage]["fallback_policy"], "none")
        self.assertEqual(profiles["extract_ocr"]["model"], "mistral-ocr-latest")
        self.assertEqual(profiles["structure"]["model"], "none")

    def test_response_explicit_effort_drift_is_rejected(self):
        for field in ("thinking", "reasoning_effort"):
            payload = _model_response("openai", "gpt-6-astra", '{"canary":"ok"}')
            payload[field] = "high"
            with self.assertRaisesRegex(MillefeuilleContractError, "effort mismatch"):
                _validate_openclaw_response(
                    payload, requested_model="openai/gpt-6-astra"
                )

    def test_old_model_alias_and_effort_cannot_enter_new_request(self):
        fixture = selection_tests.TestSummaryStageSelection()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = fixture.plan.batch.units[0].request
        for field, value in (
            ("requested_model", "openai/gpt-5.6-sol"),
            ("requested_model", "openai/gpt-6-astra-xhigh"),
            ("thinking", "high"),
        ):
            changed = deepcopy(original)
            changed[field] = value
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(MillefeuilleContractError),
            ):
                validate_model_executor_request(changed)
        for digest, model in (
            (fixture.plan.manifest.sha256, "gpt-5.6-sol"),
            ("sha256:" + "6" * 64, "gpt-6-astra"),
        ):
            packet, receipt = _approved_pair(
                fixture.root, digest, fixture.plan.batch.paper_id, 10, model=model
            )
            with self.assertRaises(MillefeuilleContractError):
                validate_gpt_summary_approval_preview(
                    **fixture.evidence,
                    source_pack_root=fixture.root,
                    artifact_root=fixture.root,
                    packet=packet,
                    receipt=receipt,
                )
