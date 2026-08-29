"""Tests for strict provider-neutral model executor envelopes."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import build_summary_execution_plan
from millefeuille.domain.model_executor import (
    MODEL_EXECUTOR_REQUEST_SCHEMA_VERSION,
    MODEL_EXECUTOR_RESULT_SCHEMA_VERSION,
    build_model_executor_request,
    build_structure_work_unit_request,
    build_summary_work_unit_request,
    materialize_model_executor_result,
    model_executor_request_sha256,
    validate_model_executor_request,
    validate_model_executor_result,
    verify_model_executor_input,
)

SPEC_DIR = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"


class TestModelExecutorContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request_schema = json.loads(
            (SPEC_DIR / "model-executor-request.schema.json").read_text(
                encoding="utf-8"
            )
        )
        cls.result_schema = json.loads(
            (SPEC_DIR / "model-executor-result.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(cls.request_schema)
        Draft202012Validator.check_schema(cls.result_schema)
        cls.request_validator = Draft202012Validator(cls.request_schema)
        cls.result_validator = Draft202012Validator(cls.result_schema)

    def _request(
        self,
        *,
        requested_model: str = "openai/gpt-5.6-sol",
        input_payload: bytes = b'{"locator":"p.1"}',
        fallback_models: list[str] | None = None,
        retry_on: list[str] | None = None,
    ) -> dict[str, object]:
        fallbacks = fallback_models or []
        retry_codes = retry_on or []
        return build_model_executor_request(
            task_kind="summarize_page",
            unit_id="page-1",
            source_locators=["p.1"],
            requested_model=requested_model,
            thinking="xhigh",
            prompt_template_id="summarize-page",
            prompt_template_version="summary-page-v1",
            input_payload=input_payload,
            output_schema_id="summary-page",
            output_schema_version="v1",
            timeout_seconds=180,
            max_attempts=1 + len(fallbacks),
            retry_on=retry_codes,
            fallback_models=fallbacks,
        )

    @staticmethod
    def _attempt(
        *,
        sequence: int = 1,
        model: str = "openai/gpt-5.6-sol",
        actual_model: str | None = "openai/gpt-5.6-sol",
        status: str = "succeeded",
        started_at: str = "2026-08-27T12:00:01Z",
        completed_at: str = "2026-08-27T12:00:02Z",
        auth_profile_ref: str | None = "openclaw:franck:openai-oauth",
        failure_code: str | None = None,
    ) -> dict[str, object]:
        return {
            "sequence": sequence,
            "model": model,
            "actual_model": actual_model,
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "auth_class": (
                "subscription_oauth" if auth_profile_ref is not None else None
            ),
            "auth_profile_ref": auth_profile_ref,
            "failure_code": failure_code,
        }

    def _success_result(
        self,
        request: dict[str, object] | None = None,
    ) -> dict[str, object]:
        selected_request = request or self._request()
        model = str(selected_request["requested_model"])
        profile_ref = (
            "openclaw:franck:openai-oauth"
            if model.startswith("openai/")
            else "openclaw:franck:xai-oauth"
        )
        output = b"{}"
        return materialize_model_executor_result(
            request=selected_request,
            status="succeeded",
            actual_model=model,
            auth_profile_ref=profile_ref,
            started_at="2026-08-27T12:00:00Z",
            completed_at="2026-08-27T12:00:03Z",
            attempts=[
                self._attempt(
                    model=model,
                    actual_model=model,
                    auth_profile_ref=profile_ref,
                )
            ],
            fallback_reason=None,
            schema_validation_status="passed",
            output_sha256="sha256:" + hashlib.sha256(output).hexdigest(),
            output_bytes=len(output),
            usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            cost_micro_usd=123,
        )

    def test_request_round_trip_is_payload_free_and_schema_valid(self):
        request = self._request()

        self.assertEqual(
            request["schema_version"], MODEL_EXECUTOR_REQUEST_SCHEMA_VERSION
        )
        self.assertEqual(request, validate_model_executor_request(request))
        self.request_validator.validate(request)
        serialized = json.dumps(request, sort_keys=True)
        self.assertNotIn("locator\":\"p.1", serialized)
        self.assertNotIn("provider_payload", serialized)
        self.assertNotIn("credentials", serialized)
        self.assertTrue(str(request["input"]["sha256"]).startswith("sha256:"))
        verify_model_executor_input(request, b'{"locator":"p.1"}')

    def test_replay_idempotency_binds_every_material_request_field(self):
        first = self._request()
        replay = self._request()
        changed_input = self._request(input_payload=b'{"locator":"p.2"}')
        changed_model = self._request(requested_model="xai/grok-4.6")

        self.assertEqual(first["idempotency_key"], replay["idempotency_key"])
        self.assertEqual(
            model_executor_request_sha256(first),
            model_executor_request_sha256(replay),
        )
        self.assertNotEqual(
            first["idempotency_key"], changed_input["idempotency_key"]
        )
        self.assertNotEqual(
            first["idempotency_key"], changed_model["idempotency_key"]
        )
        stale_identity = deepcopy(first)
        stale_identity["requested_model"] = "xai/grok-4.6"
        with self.assertRaisesRegex(MillefeuilleContractError, "idempotency key drift"):
            validate_model_executor_request(stale_identity)
        with self.assertRaisesRegex(MillefeuilleContractError, "input drift"):
            verify_model_executor_input(first, b'{"locator":"p.2"}')

    def test_requires_xhigh_as_a_separate_runtime_setting(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "must be xhigh"):
            build_model_executor_request(
                task_kind="summarize_page",
                unit_id="page-1",
                source_locators=["p.1"],
                requested_model="openai/gpt-5.6-sol",
                thinking="high",
                prompt_template_id="summarize-page",
                prompt_template_version="summary-page-v1",
                input_payload=b"payload",
                output_schema_id="summary-page",
                output_schema_version="v1",
                timeout_seconds=180,
                max_attempts=1,
                retry_on=[],
                fallback_models=[],
            )

        invalid_schema_request = self._request()
        invalid_schema_request["thinking"] = "high"
        with self.assertRaises(ValidationError):
            self.request_validator.validate(invalid_schema_request)

    def test_maps_summary_work_unit_with_explicit_model_override(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_section",
        )
        request = build_summary_work_unit_request(
            execution_plan=plan,
            work_unit={
                "unit_id": "section-s1",
                "source_locators": ["p.1#s1"],
            },
            input_payload=b'{"section":"p.1#s1"}',
            output_schema_id="summary-section",
            output_schema_version="v1",
            requested_model="xai/grok-4.6",
            thinking="xhigh",
        )

        self.assertEqual(request["task"]["kind"], "summarize_section")
        self.assertEqual(request["requested_model"], "xai/grok-4.6")
        self.assertEqual(request["thinking"], "xhigh")
        self.assertEqual(request["prompt_template"]["version"], "summary-section-v1")
        self.request_validator.validate(request)

        drifted = deepcopy(plan)
        drifted["requested_parameters"]["prompt_version"] = "other"
        with self.assertRaisesRegex(MillefeuilleContractError, "plan drift"):
            build_summary_work_unit_request(
                execution_plan=drifted,
                work_unit={
                    "unit_id": "section-s1",
                    "source_locators": ["p.1#s1"],
                },
                input_payload=b"payload",
                output_schema_id="summary-section",
                output_schema_version="v1",
            )

    def test_maps_structure_work_unit_without_a_mutable_model_default(self):
        request = build_structure_work_unit_request(
            unit_id="paper-1",
            source_locators=["p.1", "p.2"],
            input_payload=b'{"pages":["p.1","p.2"]}',
            requested_model="openai/gpt-5.6-sol",
            thinking="xhigh",
            prompt_template_version="structure-v1",
            output_schema_id="paper-structure",
            output_schema_version="v1",
        )

        self.assertEqual(request["task"]["kind"], "structure")
        self.assertEqual(request["prompt_template"]["id"], "structure")
        self.assertEqual(request["requested_model"], "openai/gpt-5.6-sol")
        self.request_validator.validate(request)

    def test_rejects_unknown_models_api_key_auth_and_hidden_fallback(self):
        with self.assertRaisesRegex(MillefeuilleContractError, "not allowed"):
            self._request(requested_model="grok-4.6")
        with self.assertRaisesRegex(MillefeuilleContractError, "not allowed"):
            self._request(requested_model="openai/gpt-5.6-sol-xhigh")

        api_key = self._request()
        api_key["authentication"] = {
            "control_plane": "provider",
            "required_class": "api_key",
            "api_key_allowed": True,
        }
        with self.assertRaisesRegex(MillefeuilleContractError, "subscription OAuth"):
            validate_model_executor_request(api_key)

        hidden_fallback = self._request()
        hidden_fallback["fallback"] = {
            "policy": "explicit",
            "models": ["xai/grok-4.6"],
        }
        with self.assertRaisesRegex(MillefeuilleContractError, "must match"):
            validate_model_executor_request(hidden_fallback)

    def test_retry_and_fallback_chain_must_be_explicit_and_deterministic(self):
        request = self._request(
            fallback_models=["xai/grok-4.6"],
            retry_on=["model_unavailable", "timeout"],
        )
        self.request_validator.validate(request)
        self.assertEqual(
            request["retry"],
            {
                "max_attempts": 2,
                "retry_on": ["model_unavailable", "timeout"],
            },
        )

        unsorted = deepcopy(request)
        unsorted["retry"]["retry_on"] = ["timeout", "model_unavailable"]
        with self.assertRaisesRegex(MillefeuilleContractError, "sorted and unique"):
            validate_model_executor_request(unsorted)

        repeated = deepcopy(request)
        repeated["fallback"]["models"] = ["openai/gpt-5.6-sol"]
        with self.assertRaisesRegex(MillefeuilleContractError, "must not repeat"):
            validate_model_executor_request(repeated)

    def test_success_result_round_trip_records_oauth_provenance(self):
        request = self._request()
        result = self._success_result(request)

        self.assertEqual(result["schema_version"], MODEL_EXECUTOR_RESULT_SCHEMA_VERSION)
        self.assertEqual(
            result,
            validate_model_executor_result(request=request, result=result),
        )
        self.result_validator.validate(result)
        self.assertEqual(result["authentication"]["class"], "subscription_oauth")
        self.assertEqual(result["actual_model"], "openai/gpt-5.6-sol")
        self.assertEqual(result["thinking"], "xhigh")
        self.assertTrue(str(result["output"]["sha256"]).startswith("sha256:"))
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("provider_payload", serialized)
        self.assertNotIn("raw_response", serialized)

    def test_explicit_fallback_result_follows_declared_chain(self):
        request = self._request(
            fallback_models=["xai/grok-4.6"],
            retry_on=["model_unavailable"],
        )
        output = b'{"summary":"ok"}'
        attempts = [
            self._attempt(
                model="openai/gpt-5.6-sol",
                actual_model=None,
                status="failed",
                completed_at="2026-08-27T12:00:02Z",
                failure_code="model_unavailable",
            ),
            self._attempt(
                sequence=2,
                model="xai/grok-4.6",
                actual_model="xai/grok-4.6",
                started_at="2026-08-27T12:00:02Z",
                completed_at="2026-08-27T12:00:04Z",
                auth_profile_ref="openclaw:franck:xai-oauth",
            ),
        ]
        result = materialize_model_executor_result(
            request=request,
            status="succeeded",
            actual_model="xai/grok-4.6",
            auth_profile_ref="openclaw:franck:xai-oauth",
            started_at="2026-08-27T12:00:00Z",
            completed_at="2026-08-27T12:00:05Z",
            attempts=attempts,
            fallback_reason="model_unavailable",
            schema_validation_status="passed",
            output_sha256="sha256:" + hashlib.sha256(output).hexdigest(),
            output_bytes=len(output),
            usage=None,
            cost_micro_usd=None,
        )

        self.assertEqual(result["actual_model"], "xai/grok-4.6")
        self.assertEqual(
            result["fallback"],
            {"used": True, "reason": "model_unavailable"},
        )
        self.result_validator.validate(result)

    def test_rejects_wrong_returned_model_auth_schema_and_request_drift(self):
        request = self._request()
        valid = self._success_result(request)

        wrong_model = deepcopy(valid)
        wrong_model["attempts"][0]["actual_model"] = "xai/grok-4.6"
        wrong_model["actual_model"] = "xai/grok-4.6"
        with self.assertRaisesRegex(MillefeuilleContractError, "model mismatch"):
            validate_model_executor_result(request=request, result=wrong_model)

        auth_drift = deepcopy(valid)
        auth_drift["authentication"]["profile_ref"] = "openclaw:franck:other-oauth"
        with self.assertRaisesRegex(MillefeuilleContractError, "auth evidence drift"):
            validate_model_executor_result(request=request, result=auth_drift)

        schema_drift = deepcopy(valid)
        schema_drift["schema_validation"]["schema_version"] = "v2"
        with self.assertRaisesRegex(MillefeuilleContractError, "version drift"):
            validate_model_executor_result(request=request, result=schema_drift)

        changed_request = self._request(input_payload=b"other")
        with self.assertRaisesRegex(MillefeuilleContractError, "request drift"):
            validate_model_executor_result(request=changed_request, result=valid)

    def test_rejects_unauthorized_fallback_transition_and_overlapping_attempts(self):
        request = self._request(
            fallback_models=["xai/grok-4.6"],
            retry_on=["timeout"],
        )
        valid = self._success_result()
        candidate = deepcopy(valid)
        candidate["request_sha256"] = model_executor_request_sha256(request)
        candidate["idempotency_key"] = request["idempotency_key"]
        candidate["actual_model"] = "xai/grok-4.6"
        candidate["authentication"] = {
            "class": "subscription_oauth",
            "profile_ref": "openclaw:franck:xai-oauth",
        }
        candidate["attempts"] = [
            self._attempt(
                actual_model=None,
                status="failed",
                failure_code="provider_error",
            ),
            self._attempt(
                sequence=2,
                model="xai/grok-4.6",
                actual_model="xai/grok-4.6",
                started_at="2026-08-27T12:00:02Z",
                completed_at="2026-08-27T12:00:03Z",
                auth_profile_ref="openclaw:franck:xai-oauth",
            ),
        ]
        candidate["fallback"] = {"used": True, "reason": "provider_error"}
        with self.assertRaisesRegex(MillefeuilleContractError, "not authorized"):
            validate_model_executor_result(request=request, result=candidate)

        candidate["attempts"][0]["failure_code"] = "timeout"
        candidate["fallback"]["reason"] = "timeout"
        candidate["attempts"][1]["started_at"] = "2026-08-27T12:00:01Z"
        with self.assertRaisesRegex(MillefeuilleContractError, "overlap"):
            validate_model_executor_result(request=request, result=candidate)

    def test_failed_result_is_bound_and_cannot_claim_output(self):
        request = self._request()
        result = materialize_model_executor_result(
            request=request,
            status="failed",
            actual_model=None,
            auth_profile_ref=None,
            started_at="2026-08-27T12:00:00Z",
            completed_at="2026-08-27T12:00:03Z",
            attempts=[
                self._attempt(
                    actual_model=None,
                    status="failed",
                    auth_profile_ref=None,
                    failure_code="auth_unavailable",
                )
            ],
            fallback_reason=None,
            schema_validation_status="not_run",
            output_sha256=None,
            output_bytes=None,
            usage=None,
            cost_micro_usd=None,
        )
        self.result_validator.validate(result)
        self.assertEqual(result["output"], {"sha256": None, "bytes": None})
        self.assertEqual(result["authentication"], {"class": None, "profile_ref": None})

        forged_output = deepcopy(result)
        forged_output["output"] = {
            "sha256": "sha256:" + "0" * 64,
            "bytes": 1,
        }
        with self.assertRaisesRegex(MillefeuilleContractError, "must not claim"):
            validate_model_executor_result(request=request, result=forged_output)
        with self.assertRaises(ValidationError):
            self.result_validator.validate(forged_output)

    def test_generic_schemas_reject_unexpected_payload_fields(self):
        request = self._request()
        request["provider_payload"] = {}
        with self.assertRaises(ValidationError):
            self.request_validator.validate(request)

        result = self._success_result()
        result["raw_response"] = {}
        with self.assertRaises(ValidationError):
            self.result_validator.validate(result)


if __name__ == "__main__":
    unittest.main()
