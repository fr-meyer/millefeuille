"""Whole-batch, in-memory validation for GPT summary outputs."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import build_summary_execution_plan
from millefeuille.domain.model_executor import (
    build_summary_work_unit_request,
    materialize_model_executor_result,
)
from millefeuille.domain.summary_dispatch import (
    SummaryDispatchBatch,
    SummaryDispatchUnit,
    _bind_prompt,
)
from millefeuille.domain.summary_results import (
    accept_summary_execution_batch,
    validate_summary_unit_payload,
)


class TestSummaryResults(unittest.TestCase):
    def _unit(
        self,
        stage: str,
        unit_id: str,
        locator: str,
        *,
        preparation_digest: str = "sha256:" + "a" * 64,
    ) -> SummaryDispatchUnit:
        prompt = _bind_prompt(
            preparation_digest=preparation_digest,
            paper_id="zotero-ITEM1",
            stage=stage,
            unit_id=unit_id,
            locators=(locator,),
            prompt=f"Private prompt for {locator}".encode(),
        )
        schema_id = {
            "summarize_page": "summary-page",
            "summarize_section": "summary-section",
        }[stage]
        request = build_summary_work_unit_request(
            execution_plan=build_summary_execution_plan(
                profile="research-default", stage=stage
            ),
            work_unit={"unit_id": unit_id, "source_locators": [locator]},
            input_payload=prompt,
            output_schema_id=schema_id,
            output_schema_version="v1",
            requested_model="openai/gpt-5.6-sol",
            fallback_models=[],
        )
        return SummaryDispatchUnit(
            paper_id="zotero-ITEM1",
            stage=stage,
            unit_id=unit_id,
            source_locators=(locator,),
            request=request,
            input_payload=prompt,
        )

    @staticmethod
    def _output(unit: SummaryDispatchUnit, *, locator: str | None = None) -> bytes:
        return json.dumps(
            {
                "schema_version": "v1",
                "paper_id": unit.paper_id,
                "stage": unit.stage,
                "unit_id": unit.unit_id,
                "summary": "Private accepted summary.",
                "source_locators": [locator or unit.source_locators[0]],
            },
            separators=(",", ":"),
        ).encode()

    @staticmethod
    def _execution(unit: SummaryDispatchUnit, output: bytes) -> OpenClawModelExecution:
        profile = "openclaw:franck:openai-oauth"
        result = materialize_model_executor_result(
            request=unit.request,
            status="succeeded",
            actual_model="openai/gpt-5.6-sol",
            auth_profile_ref=profile,
            started_at="2026-09-25T00:00:00Z",
            completed_at="2026-09-25T00:00:02Z",
            attempts=[
                {
                    "sequence": 1,
                    "model": "openai/gpt-5.6-sol",
                    "actual_model": "openai/gpt-5.6-sol",
                    "status": "succeeded",
                    "started_at": "2026-09-25T00:00:00Z",
                    "completed_at": "2026-09-25T00:00:02Z",
                    "auth_class": "subscription_oauth",
                    "auth_profile_ref": profile,
                    "failure_code": None,
                }
            ],
            fallback_reason=None,
            schema_validation_status="passed",
            output_sha256="sha256:" + hashlib.sha256(output).hexdigest(),
            output_bytes=len(output),
            usage=None,
            cost_micro_usd=None,
        )
        return OpenClawModelExecution(result=result, output=output)

    def _batch(self) -> SummaryDispatchBatch:
        return SummaryDispatchBatch(
            paper_id="zotero-ITEM1",
            preparation_sha256="sha256:" + "a" * 64,
            units=(
                self._unit("summarize_page", "page-1", "p.1"),
                self._unit("summarize_section", "section-s1", "p.1#s1"),
            ),
        )

    def test_accepts_exact_batch_without_exposing_private_text_in_repr(self):
        batch = self._batch()
        executions = {
            (unit.stage, unit.unit_id): self._execution(unit, self._output(unit))
            for unit in batch.units
        }
        accepted = accept_summary_execution_batch(batch=batch, executions=executions)
        self.assertEqual(len(accepted.units), 2)
        self.assertEqual(accepted.units[0].summary, "Private accepted summary.")
        self.assertEqual(accepted.units[1].source_locators, ("p.1#s1",))
        self.assertNotIn("Private accepted summary", repr(accepted))
        self.assertEqual(accepted.units[0].executor_result["status"], "succeeded")

    def test_rejects_missing_extra_and_drifted_result_bindings(self):
        batch = self._batch()
        executions = {
            (unit.stage, unit.unit_id): self._execution(unit, self._output(unit))
            for unit in batch.units
        }
        first_key = (batch.units[0].stage, batch.units[0].unit_id)
        with self.assertRaisesRegex(MillefeuilleContractError, "coverage drift"):
            accept_summary_execution_batch(
                batch=batch,
                executions={
                    key: value for key, value in executions.items() if key != first_key
                },
            )
        with self.assertRaisesRegex(MillefeuilleContractError, "coverage drift"):
            accept_summary_execution_batch(
                batch=batch,
                executions={
                    **executions,
                    ("summarize_page", "page-99"): executions[first_key],
                },
            )
        drifted = OpenClawModelExecution(
            result=executions[first_key].result,
            output=b"{}",
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "output binding drift"):
            accept_summary_execution_batch(
                batch=batch,
                executions={**executions, first_key: drifted},
            )

    def test_rejects_unapproved_citations_and_schema_drift(self):
        batch = self._batch()
        unit = batch.units[0]
        payload = json.loads(self._output(unit))
        payload["source_locators"] = ["p.99"]
        with self.assertRaisesRegex(MillefeuilleContractError, "source locators"):
            validate_summary_unit_payload(unit, payload)
        payload["source_locators"] = ["p.1"]
        payload["schema_version"] = "v2"
        with self.assertRaisesRegex(MillefeuilleContractError, "schema drift"):
            validate_summary_unit_payload(unit, payload)

    def test_rejects_unit_bound_to_another_preparation(self):
        original = self._batch()
        other = self._unit(
            "summarize_section",
            "section-s1",
            "p.1#s1",
            preparation_digest="sha256:" + "b" * 64,
        )
        batch = SummaryDispatchBatch(
            paper_id=original.paper_id,
            preparation_sha256=original.preparation_sha256,
            units=(original.units[0], other),
        )
        executions = {
            (unit.stage, unit.unit_id): self._execution(unit, self._output(unit))
            for unit in batch.units
        }
        with self.assertRaisesRegex(
            MillefeuilleContractError, "preparation binding drift"
        ):
            accept_summary_execution_batch(batch=batch, executions=executions)

    def test_rejects_whole_batch_when_later_unit_fails(self):
        batch = self._batch()
        executions = {
            (unit.stage, unit.unit_id): self._execution(unit, self._output(unit))
            for unit in batch.units
        }
        later = batch.units[1]
        failed = materialize_model_executor_result(
            request=later.request,
            status="failed",
            actual_model=None,
            auth_profile_ref=None,
            started_at="2026-09-25T00:00:00Z",
            completed_at="2026-09-25T00:00:02Z",
            attempts=[
                {
                    "sequence": 1,
                    "model": "openai/gpt-5.6-sol",
                    "actual_model": None,
                    "status": "failed",
                    "started_at": "2026-09-25T00:00:00Z",
                    "completed_at": "2026-09-25T00:00:02Z",
                    "auth_class": None,
                    "auth_profile_ref": None,
                    "failure_code": "provider_error",
                }
            ],
            fallback_reason=None,
            schema_validation_status="not_run",
            output_sha256=None,
            output_bytes=None,
            usage=None,
            cost_micro_usd=None,
        )
        executions[(later.stage, later.unit_id)] = OpenClawModelExecution(
            result=failed, output=None
        )
        with self.assertRaisesRegex(MillefeuilleContractError, "did not succeed"):
            accept_summary_execution_batch(batch=batch, executions=executions)

    def test_rejects_duplicate_json_fields_and_tampered_request(self):
        batch = self._batch()
        executions = {
            (unit.stage, unit.unit_id): self._execution(unit, self._output(unit))
            for unit in batch.units
        }
        first = batch.units[0]
        duplicate_output = self._output(first).replace(
            b'"schema_version":"v1",',
            b'"schema_version":"v1","schema_version":"v1",',
        )
        key = (first.stage, first.unit_id)
        with self.assertRaisesRegex(MillefeuilleContractError, "strict JSON"):
            accept_summary_execution_batch(
                batch=batch,
                executions={
                    **executions,
                    key: self._execution(first, duplicate_output),
                },
            )
        drifted_unit = deepcopy(first)
        drifted_unit.request["requested_model"] = "xai/grok-4.6"
        with self.assertRaises(MillefeuilleContractError):
            validate_summary_unit_payload(drifted_unit, json.loads(self._output(first)))
