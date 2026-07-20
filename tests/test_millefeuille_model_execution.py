"""Tests for deterministic no-call model execution plans."""

from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, ValidationError

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import (
    build_summary_execution_plan,
    materialize_model_provenance_record,
    materialize_model_provenance_record_from_files,
    validate_model_provenance_record,
)
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE


class ModelExecutionPlanTests(unittest.TestCase):
    @staticmethod
    def _credential_tokens() -> tuple[str, ...]:
        return (
            "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz123456",
            "AI" + "za" + "abcdefghijklmnopqrstuvwx",
            "AK" + "IA" + "ABCDEFGHIJKLMNOP",
            "github_" + "pat_" + "abcdefghijklmnopqrstuvwxyz123456",
            "gl" + "pat-" + "abcdefghijklmnopqrstuvwxyz123456",
            "npm" + "_" + "abcdefghijklmnopqrstuvwxyz123456",
            "dop_" + "v1_" + "a" * 64,
            "rk_" + "live_" + "abcdefghijklmnopqrstuvwxyz",
            "hf" + "_" + "abcdefghijklmnopqrstuvwxyz123456",
            "xox" + "b-" + "abcdefghijklmnopqrstuvwxyz123456",
            "eyJabcdefghijklmno"
            + ".eyJpqrstuvwxyzAB"
            + ".cdefghijklmnop",
        )

    @classmethod
    def _ref_payload_tokens(cls) -> tuple[str, ...]:
        credential_tokens = cls._credential_tokens()
        return (
            *credential_tokens,
            *(f"archive_{token}" for token in credential_tokens),
            *(f"{token}-suffix" for token in credential_tokens),
        )

    @classmethod
    def _payload_markers(cls) -> tuple[str, ...]:
        return (
            *cls._ref_payload_tokens(),
            "authorization: basic placeholder",
            "Bearer abcdefghijklmnop",
            "%PDF-1.7",
            "data:application/pdf;base64,AAA",
        )

    def _research_execution_evidence(self) -> dict[str, object]:
        return {
            "schema_version": "millefeuille-model-execution-evidence/v0.1",
            "profile": "research-default",
            "stage": "summarize_full_paper",
            "requested_model": "openai/gpt-5.6-sol",
            "resolved_model": "openai/gpt-5.6-sol",
            "provider": "openai",
            "backend": "chat",
            "reasoning_effort": "xhigh",
            "fast_mode": "off",
            "prompt_version": "summary-full-paper-v1",
            "fallback_used": False,
            "input_refs": [
                "structure/structure.json",
                "structure/outline.md",
            ],
            "output_refs": [
                "summaries/hierarchical-summary.json",
                "summaries/texts/full-paper.md",
            ],
            "usage": {
                "input_tokens": 120,
                "output_tokens": 34,
                "total_tokens": 154,
            },
            "quality_warnings": [
                {
                    "code": "short_abstract",
                    "severity": "warning",
                    "ref": "structure/structure.json",
                }
            ],
        }

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

        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        generated = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        validator.validate(generated)
        validator.validate(
            build_summary_execution_plan(
                profile="offline-preview",
                stage="summarize_page",
            )
        )
        malformed = deepcopy(generated)
        del malformed["authentication"]["lane"]
        with self.assertRaises(ValidationError):
            validator.validate(malformed)
        incomplete_provenance = deepcopy(generated)
        incomplete_provenance["provenance_contract"]["required_fields"].pop()
        with self.assertRaises(ValidationError):
            validator.validate(incomplete_provenance)

        execution = schema["properties"]["execution"]["properties"]
        self.assertFalse(execution["provider_call_permitted"]["const"])
        self.assertFalse(execution["provider_call_performed"]["const"])
        self.assertFalse(execution["ready_for_approved_live_execution"]["const"])

    def test_model_provenance_materializes_strict_no_payload_record(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()

        self.assertEqual(
            plan["requested_parameters"],
            {
                "fast_mode": "off",
                "prompt_version": "summary-full-paper-v1",
                "reasoning_effort": "xhigh",
                "record_usage": True,
            },
        )

        record = materialize_model_provenance_record(
            execution_plan=plan,
            execution_evidence=evidence,
        )

        self.assertEqual(
            record["schema_version"],
            "millefeuille-model-provenance/v0.1",
        )
        self.assertEqual(record["profile"], plan["profile"])
        self.assertEqual(record["stage"], plan["stage"])
        self.assertEqual(record["requested_model"], "openai/gpt-5.6-sol")
        self.assertEqual(record["resolved_model"], "openai/gpt-5.6-sol")
        self.assertEqual(record["usage"]["total_tokens"], 154)
        compatible_evidence = self._research_execution_evidence()
        compatible_evidence["output_refs"] = [
            "summaries/hierarchical-summary.json",
            "summaries/texts",
            "summaries/texts/section-methods.md",
            "summaries/texts/.hidden.md",
            "summaries/texts/_internal.md",
            "summaries/texts/-draft.md",
        ]
        compatible_record = materialize_model_provenance_record(
            execution_plan=plan,
            execution_evidence=compatible_evidence,
        )
        self.assertEqual(
            compatible_record["output_refs"],
            compatible_evidence["output_refs"],
        )
        self.assertNotIn("fallback_used", record)
        self.assertNotIn("authentication", record)
        self.assertNotIn("execution", record)

    def test_all_bundled_summary_plans_have_allowed_parameters_and_materialize(self):
        allowed_parameters = {
            "temperature",
            "reasoning_effort",
            "fast_mode",
            "prompt_version",
            "record_usage",
        }
        summary_stages = (
            "summarize_page",
            "summarize_section",
            "summarize_full_paper",
        )

        for profile in ("offline-preview", "research-default"):
            for stage in summary_stages:
                with self.subTest(profile=profile, stage=stage):
                    plan = build_summary_execution_plan(
                        profile=profile,
                        stage=stage,
                    )
                    requested_parameters = plan["requested_parameters"]
                    self.assertTrue(requested_parameters)
                    self.assertLessEqual(
                        set(requested_parameters),
                        allowed_parameters,
                    )
                    self.assertIn("record_usage", requested_parameters)

                    evidence = self._research_execution_evidence()
                    evidence.update(
                        {
                            "profile": profile,
                            "stage": stage,
                            "requested_model": plan["requested_model"],
                            "resolved_model": plan["requested_model"],
                            "provider": plan["provider"],
                            "backend": plan["backend"],
                            "reasoning_effort": requested_parameters.get(
                                "reasoning_effort"
                            ),
                            "fast_mode": requested_parameters.get("fast_mode"),
                            "prompt_version": requested_parameters.get(
                                "prompt_version"
                            ),
                        }
                    )
                    record = materialize_model_provenance_record(
                        execution_plan=plan,
                        execution_evidence=evidence,
                    )
                    self.assertEqual(record["profile"], profile)
                    self.assertEqual(record["stage"], stage)

    def test_model_provenance_rejects_unknown_private_or_malformed_evidence(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        cases = [
            ("prompt", "hidden prompt", "forbidden field"),
            ("resolved_model", "openai/other", "resolved_model"),
            (
                "input_refs",
                ["structure/structure.json", "structure/structure.json"],
                "duplicate",
            ),
            ("output_refs", ["../leak.json"], "trusted relative artifact"),
            (
                "output_refs",
                ["summaries/../leak.json"],
                "trusted relative artifact",
            ),
            ("output_refs", ["private/output.json"], "trusted relative artifact"),
            ("output_refs", ["summaries"], "trusted relative artifact"),
            (
                "output_refs",
                [
                    "structure/structure.json",
                    "summaries/texts/full-paper.md",
                ],
                "shared refs",
            ),
            (
                "quality_warnings",
                [
                    {
                        "code": "warning",
                        "severity": "warning",
                        "message": "free text",
                    }
                ],
                "unknown fields",
            ),
        ]
        for field_name, value, error in cases:
            with self.subTest(field_name=field_name):
                evidence = self._research_execution_evidence()
                evidence[field_name] = value
                with self.assertRaisesRegex(MillefeuilleContractError, error):
                    materialize_model_provenance_record(
                        execution_plan=plan,
                        execution_evidence=evidence,
                    )

        bad_usage = self._research_execution_evidence()
        bad_usage["usage"] = {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 3,
        }
        with self.assertRaisesRegex(MillefeuilleContractError, "total_tokens"):
            materialize_model_provenance_record(
                execution_plan=plan,
                execution_evidence=bad_usage,
            )

    def test_model_provenance_rejects_whitespace_padded_controls_and_refs(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        cases: list[tuple[str, object]] = [
            ("profile", " research-default"),
            ("profile", "research-default\u0085"),
            ("reasoning_effort", "xhigh "),
            ("prompt_version", "summary-full-paper-v1\u001c"),
            (
                "input_refs",
                [
                    " structure/structure.json",
                    "structure/outline.md",
                ],
            ),
            (
                "output_refs",
                [
                    "summaries/hierarchical-summary.json",
                    "summaries/texts/full-paper.md ",
                ],
            ),
        ]

        for field_name, value in cases:
            with (
                self.subTest(field_name=field_name),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "leading or trailing whitespace",
                ),
            ):
                evidence = self._research_execution_evidence()
                evidence[field_name] = value
                materialize_model_provenance_record(
                    execution_plan=plan,
                    execution_evidence=evidence,
                )

    def test_model_provenance_drift_errors_do_not_reflect_values(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()
        evidence["prompt_version"] = "confidential-research-version"

        with self.assertRaises(MillefeuilleContractError) as raised:
            materialize_model_provenance_record(
                execution_plan=plan,
                execution_evidence=evidence,
            )

        self.assertEqual(
            str(raised.exception),
            "model provenance prompt_version drift",
        )
        self.assertNotIn("confidential-research-version", str(raised.exception))
        self.assertNotIn("summary-full-paper-v1", str(raised.exception))

        no_reflection_cases = [
            (
                "unknown field",
                {"private-case-name": "private-case-value"},
                "private-case-name",
                "private-case-value",
            ),
            (
                "duplicate ref",
                {
                    "input_refs": [
                        "private/case-reference.json",
                        "private/case-reference.json",
                    ]
                },
                "private/case-reference.json",
                None,
            ),
            (
                "shared ref",
                {
                    "input_refs": ["private/shared-reference.json"],
                    "output_refs": ["private/shared-reference.json"],
                },
                "private/shared-reference.json",
                None,
            ),
        ]
        for label, changes, private_value, second_private_value in no_reflection_cases:
            with self.subTest(label=label):
                malformed = self._research_execution_evidence()
                malformed.update(changes)
                with self.assertRaises(MillefeuilleContractError) as nested_raised:
                    materialize_model_provenance_record(
                        execution_plan=plan,
                        execution_evidence=malformed,
                    )
                error = str(nested_raised.exception)
                self.assertNotIn(private_value, error)
                if second_private_value is not None:
                    self.assertNotIn(second_private_value, error)

    def test_model_evidence_and_record_schemas_match_lexical_semantic_contract(self):
        spec_dir = (
            Path(__file__).resolve().parents[1]
            / "specs"
            / "millefeuille-pipeline"
        )
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()
        record = materialize_model_provenance_record(
            execution_plan=plan,
            execution_evidence=evidence,
        )
        fixtures = {
            "model-execution-evidence.schema.json": evidence,
            "model-provenance-record.schema.json": record,
        }

        for schema_name, valid_payload in fixtures.items():
            with self.subTest(schema=schema_name):
                schema = json.loads(
                    (spec_dir / schema_name).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)
                validator = Draft202012Validator(schema)
                validator.validate(valid_payload)
                semantic = schema["x-millefeuille-semantic-validation"]
                self.assertTrue(semantic["required"])
                self.assertEqual(semantic["arithmetic"], "exact-integer")
                self.assertEqual(semantic["maximum_integer"], 9007199254740991)
                self.assertEqual(
                    semantic["assertions"],
                    [
                        "usage.total_tokens == usage.input_tokens + "
                        "usage.output_tokens",
                        "intersection(input_refs, output_refs) == []",
                    ],
                )
                self.assertEqual(
                    semantic["first_party_validator"]["requires"],
                    (
                        ["execution_plan", "execution_evidence"]
                        if schema_name == "model-execution-evidence.schema.json"
                        else ["model_provenance_record"]
                    ),
                )

                malformed_payloads = []
                for boundary_whitespace in (
                    " ",
                    "\u001c",
                    "\u001f",
                    "\u0085",
                    "\u00a0",
                    "\u3000",
                ):
                    padded_profile = deepcopy(valid_payload)
                    padded_profile["profile"] = (
                        boundary_whitespace + "research-default"
                    )
                    malformed_payloads.append(padded_profile)
                    padded_prompt_version = deepcopy(valid_payload)
                    padded_prompt_version["prompt_version"] = (
                        "summary-v1" + boundary_whitespace
                    )
                    malformed_payloads.append(padded_prompt_version)
                newline_ref = deepcopy(valid_payload)
                newline_ref["input_refs"][0] += "\n"
                malformed_payloads.append(newline_ref)
                untrusted_namespace_ref = deepcopy(valid_payload)
                untrusted_namespace_ref["input_refs"][0] = "private/artifact.json"
                malformed_payloads.append(untrusted_namespace_ref)
                for bare_namespace in ("structure", "summaries"):
                    bare_namespace_payload = deepcopy(valid_payload)
                    bare_namespace_payload["input_refs"][0] = bare_namespace
                    malformed_payloads.append(bare_namespace_payload)
                for credential_token in self._ref_payload_tokens():
                    credential_ref = (
                        f"summaries/texts/{credential_token}.md"
                    )
                    for ref_field in ("input_refs", "output_refs"):
                        credential_payload = deepcopy(valid_payload)
                        credential_payload[ref_field] = [credential_ref]
                        malformed_payloads.append(credential_payload)
                    credential_warning = deepcopy(valid_payload)
                    credential_warning["quality_warnings"][0]["ref"] = (
                        credential_ref
                    )
                    malformed_payloads.append(credential_warning)
                for payload_marker in self._payload_markers():
                    payload_marker_provider = deepcopy(valid_payload)
                    payload_marker_provider["provider"] = payload_marker
                    malformed_payloads.append(payload_marker_provider)
                credential_warning_code = deepcopy(valid_payload)
                credential_warning_code["quality_warnings"][0]["code"] = (
                    self._credential_tokens()[4]
                )
                malformed_payloads.append(credential_warning_code)
                trailing_slash_ref = deepcopy(valid_payload)
                trailing_slash_ref["input_refs"][0] = "artifact/"
                malformed_payloads.append(trailing_slash_ref)
                for traversal_ref in (
                    "summaries/../artifact.json",
                    "summaries/./artifact.json",
                ):
                    traversal_payload = deepcopy(valid_payload)
                    traversal_payload["input_refs"][0] = traversal_ref
                    malformed_payloads.append(traversal_payload)
                newline_warning_code = deepcopy(valid_payload)
                newline_warning_code["quality_warnings"][0]["code"] += "\n"
                malformed_payloads.append(newline_warning_code)
                oversized_tokens = deepcopy(valid_payload)
                oversized_tokens["usage"] = {
                    "input_tokens": 9007199254740992,
                    "output_tokens": 0,
                    "total_tokens": 9007199254740992,
                }
                malformed_payloads.append(oversized_tokens)

                for malformed in malformed_payloads:
                    with self.assertRaises(ValidationError):
                        validator.validate(malformed)

                byte_order_mark_is_not_boundary_whitespace = deepcopy(valid_payload)
                byte_order_mark_is_not_boundary_whitespace["prompt_version"] = (
                    "\ufeffsummary-v1\ufeff"
                )
                validator.validate(byte_order_mark_is_not_boundary_whitespace)

                compatible_refs = deepcopy(valid_payload)
                compatible_refs["output_refs"] = [
                    "summaries/hierarchical-summary.json",
                    "summaries/texts",
                    "summaries/texts/section-methods.md",
                    "summaries/texts/.hidden.md",
                    "summaries/texts/_internal.md",
                    "summaries/texts/-draft.md",
                ]
                validator.validate(compatible_refs)

                structurally_valid_bad_total = deepcopy(valid_payload)
                structurally_valid_bad_total["usage"]["total_tokens"] += 1
                validator.validate(structurally_valid_bad_total)

                structurally_valid_overlapping_refs = deepcopy(valid_payload)
                structurally_valid_overlapping_refs["output_refs"][0] = (
                    structurally_valid_overlapping_refs["input_refs"][0]
                )
                validator.validate(structurally_valid_overlapping_refs)

        byte_order_mark_record = deepcopy(record)
        byte_order_mark_record["prompt_version"] = "\ufeffsummary-v1\ufeff"
        self.assertEqual(
            validate_model_provenance_record(byte_order_mark_record)[
                "prompt_version"
            ],
            "\ufeffsummary-v1\ufeff",
        )

        maximum_record = deepcopy(record)
        maximum_record["usage"] = {
            "input_tokens": 9007199254740991,
            "output_tokens": 0,
            "total_tokens": 9007199254740991,
        }
        self.assertEqual(
            validate_model_provenance_record(maximum_record)["usage"][
                "total_tokens"
            ],
            9007199254740991,
        )

        oversized_record = deepcopy(record)
        oversized_record["usage"] = {
            "input_tokens": 9007199254740992,
            "output_tokens": 0,
            "total_tokens": 9007199254740992,
        }
        with self.assertRaisesRegex(MillefeuilleContractError, "safe-integer"):
            validate_model_provenance_record(oversized_record)

        trailing_slash_record = deepcopy(record)
        trailing_slash_record["input_refs"][0] = "artifact/"
        with self.assertRaisesRegex(
            MillefeuilleContractError,
            "trusted relative artifact",
        ):
            validate_model_provenance_record(trailing_slash_record)

        traversal_record = deepcopy(record)
        traversal_record["input_refs"][0] = "summaries/../artifact.json"
        with self.assertRaisesRegex(
            MillefeuilleContractError,
            "trusted relative artifact",
        ):
            validate_model_provenance_record(traversal_record)

        bare_namespace_record = deepcopy(record)
        bare_namespace_record["input_refs"][0] = "summaries"
        with self.assertRaisesRegex(
            MillefeuilleContractError,
            "trusted relative artifact",
        ):
            validate_model_provenance_record(bare_namespace_record)

        overlapping_record = deepcopy(record)
        overlapping_record["output_refs"][0] = overlapping_record["input_refs"][0]
        with self.assertRaisesRegex(MillefeuilleContractError, "shared refs"):
            validate_model_provenance_record(overlapping_record)

        bad_evidence = deepcopy(evidence)
        bad_evidence["usage"]["total_tokens"] += 1
        with self.assertRaisesRegex(MillefeuilleContractError, "total_tokens"):
            materialize_model_provenance_record(
                execution_plan=plan,
                execution_evidence=bad_evidence,
            )

        bad_record = deepcopy(record)
        bad_record["usage"]["total_tokens"] += 1
        with self.assertRaisesRegex(MillefeuilleContractError, "total_tokens"):
            validate_model_provenance_record(bad_record)

    def test_model_provenance_rejects_incomplete_or_relaxed_execution_plans(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        cases: list[tuple[str, dict[str, object], str]] = []

        unknown_top = deepcopy(plan)
        unknown_top["approval"] = True
        cases.append(("unknown top-level field", unknown_top, "unknown fields"))

        missing_control = deepcopy(plan)
        del missing_control["execution"]["provider_call_permitted"]
        cases.append(("missing no-call control", missing_control, "missing required"))

        relaxed_control = deepcopy(plan)
        relaxed_control["execution"]["provider_call_permitted"] = True
        cases.append(("provider call permitted", relaxed_control, "must not permit"))

        unknown_auth = deepcopy(plan)
        unknown_auth["authentication"]["token"] = "ignored"
        cases.append(("unknown authentication field", unknown_auth, "unknown fields"))

        missing_usage_control = deepcopy(plan)
        del missing_usage_control["requested_parameters"]["record_usage"]
        cases.append(
            ("missing usage control", missing_usage_control, "missing required")
        )

        unknown_parameter = deepcopy(plan)
        unknown_parameter["requested_parameters"]["untrusted"] = "ignored"
        cases.append(
            ("unknown requested parameter", unknown_parameter, "unknown fields")
        )

        false_fixture_claim = deepcopy(plan)
        false_fixture_claim["execution"]["fixture_only"] = True
        cases.append(("false fixture claim", false_fixture_claim, "execution lane"))

        changed_provenance_contract = deepcopy(plan)
        changed_provenance_contract["provenance_contract"][
            "required_fields"
        ].pop()
        cases.append(
            (
                "incomplete provenance contract",
                changed_provenance_contract,
                "strict contract",
            )
        )

        for label, malformed_plan, error in cases:
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(MillefeuilleContractError, error),
            ):
                materialize_model_provenance_record(
                    execution_plan=malformed_plan,
                    execution_evidence=self._research_execution_evidence(),
                )

    def test_model_provenance_rejects_noncanonical_or_invalid_fallback_resolution(self):
        bundle = deepcopy(DEFAULT_MODEL_PROFILE_BUNDLE)
        stage_config = bundle["profiles"]["research-default"]["summarize_full_paper"]
        stage_config["fallback_policy"] = "explicit"
        stage_config["fallback_model"] = "openai/gpt-5.4"
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
            bundle=bundle,
        )
        evidence = self._research_execution_evidence()
        evidence["resolved_model"] = "openai/gpt-5.4"
        evidence["fallback_used"] = True

        with self.assertRaisesRegex(MillefeuilleContractError, "canonical bundled"):
            materialize_model_provenance_record(
                execution_plan=plan,
                execution_evidence=evidence,
            )

        canonical = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        invalid_evidence = self._research_execution_evidence()
        invalid_evidence["resolved_model"] = "openai/gpt-5.4"
        invalid_evidence["fallback_used"] = True
        with self.assertRaisesRegex(MillefeuilleContractError, "fallback_used"):
            materialize_model_provenance_record(
                execution_plan=canonical,
                execution_evidence=invalid_evidence,
            )

    def test_model_provenance_rejects_payload_bearing_plan_values(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()
        marker = "Bearer abcdefghijklmnop"
        plan["requested_parameters"]["prompt_version"] = marker
        evidence["prompt_version"] = marker

        with self.assertRaisesRegex(MillefeuilleContractError, "payload marker"):
            materialize_model_provenance_record(
                execution_plan=plan,
                execution_evidence=evidence,
            )

        canonical_plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        canonical_record = materialize_model_provenance_record(
            execution_plan=build_summary_execution_plan(
                profile="research-default",
                stage="summarize_full_paper",
            ),
            execution_evidence=self._research_execution_evidence(),
        )
        for credential_token in self._ref_payload_tokens():
            credential_ref = f"summaries/texts/{credential_token}.md"
            for route in ("input_refs", "output_refs", "quality_warnings"):
                with self.subTest(credential_token=credential_token, route=route):
                    credential_evidence = self._research_execution_evidence()
                    if route == "quality_warnings":
                        credential_evidence[route][0]["ref"] = credential_ref
                    else:
                        credential_evidence[route] = [credential_ref]
                    with self.assertRaisesRegex(
                        MillefeuilleContractError,
                        "payload marker",
                    ) as raised:
                        materialize_model_provenance_record(
                            execution_plan=canonical_plan,
                            execution_evidence=credential_evidence,
                        )
                    self.assertNotIn(credential_ref, str(raised.exception))

                    credential_record = deepcopy(canonical_record)
                    if route == "quality_warnings":
                        credential_record[route][0]["ref"] = credential_ref
                    else:
                        credential_record[route] = [credential_ref]
                    with self.assertRaisesRegex(
                        MillefeuilleContractError,
                        "payload marker",
                    ) as record_raised:
                        validate_model_provenance_record(credential_record)
                    self.assertNotIn(credential_ref, str(record_raised.exception))

        for payload_marker in self._payload_markers():
            with self.subTest(payload_marker=payload_marker):
                payload_evidence = self._research_execution_evidence()
                payload_evidence["provider"] = payload_marker
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "payload marker",
                ) as payload_raised:
                    materialize_model_provenance_record(
                        execution_plan=canonical_plan,
                        execution_evidence=payload_evidence,
                    )
                self.assertNotIn(payload_marker, str(payload_raised.exception))

                payload_record = deepcopy(canonical_record)
                payload_record["provider"] = payload_marker
                with self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "payload marker",
                ) as payload_record_raised:
                    validate_model_provenance_record(payload_record)
                self.assertNotIn(
                    payload_marker,
                    str(payload_record_raised.exception),
                )

        opaque_ref = "summaries/texts/opaque-token-shaped-value.md"
        for route in ("input_refs", "output_refs", "quality_warnings"):
            with self.subTest(opaque_route=route):
                opaque_evidence = self._research_execution_evidence()
                if route == "quality_warnings":
                    opaque_evidence[route][0]["ref"] = opaque_ref
                else:
                    opaque_evidence[route] = [opaque_ref]
                opaque_record = materialize_model_provenance_record(
                    execution_plan=canonical_plan,
                    execution_evidence=opaque_evidence,
                )
                if route == "quality_warnings":
                    self.assertEqual(
                        opaque_record[route][0]["ref"],
                        opaque_ref,
                    )
                else:
                    self.assertEqual(opaque_record[route], [opaque_ref])

        benign_evidence = self._research_execution_evidence()
        benign_evidence["quality_warnings"] = [
            {
                "code": "risk-assessment",
                "severity": "warning",
                "ref": "structure/structure.json",
            },
            {"code": "task-generated", "severity": "info"},
        ]
        benign_record = materialize_model_provenance_record(
            execution_plan=canonical_plan,
            execution_evidence=benign_evidence,
        )
        self.assertEqual(
            [warning["code"] for warning in benign_record["quality_warnings"]],
            ["risk-assessment", "task-generated"],
        )

    def test_models_cli_materializes_provenance_from_plan_and_evidence_files(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            evidence_path = root / "evidence.json"
            output_path = root / "provenance" / "model-provenance.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            stdout = StringIO()
            exit_code = run_stage_cli(
                [
                    "models",
                    "--provenance",
                    "--plan-file",
                    str(plan_path),
                    "--execution-evidence",
                    str(evidence_path),
                    "--output",
                    str(output_path),
                    "--json",
                ],
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload, written)
            self.assertEqual(
                payload["schema_version"],
                "millefeuille-model-provenance/v0.1",
            )

    def test_models_cli_rejects_credential_ref_without_reflection_or_output(self):
        secret_ref = f"summaries/texts/{self._credential_tokens()[0]}.md"
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()
        evidence["input_refs"] = [secret_ref]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            evidence_path = root / "evidence.json"
            output_path = root / "provenance" / "model-provenance.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_stage_cli(
                [
                    "models",
                    "--provenance",
                    "--plan-file",
                    str(plan_path),
                    "--execution-evidence",
                    str(evidence_path),
                    "--output",
                    str(output_path),
                    "--json",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("payload marker", stderr.getvalue())
            self.assertNotIn(secret_ref, stdout.getvalue())
            self.assertNotIn(secret_ref, stderr.getvalue())
            self.assertFalse(output_path.exists())
            self.assertFalse(output_path.parent.exists())

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "requires no-follow writes")
    def test_model_provenance_output_rejects_symlinked_paths_and_existing_files(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            evidence_path = root / "evidence.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            outside = root / "outside.json"
            dangling = root / "dangling.json"
            dangling.symlink_to(outside)
            with self.assertRaisesRegex(MillefeuilleContractError, "symbolic link"):
                materialize_model_provenance_record_from_files(
                    execution_plan_path=plan_path,
                    execution_evidence_path=evidence_path,
                    output_path=dangling,
                )
            self.assertFalse(outside.exists())

            outside_dir = root / "outside"
            outside_dir.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(outside_dir, target_is_directory=True)
            with self.assertRaisesRegex(MillefeuilleContractError, "symbolic links"):
                materialize_model_provenance_record_from_files(
                    execution_plan_path=plan_path,
                    execution_evidence_path=evidence_path,
                    output_path=linked_parent / "provenance.json",
                )
            self.assertFalse((outside_dir / "provenance.json").exists())

            existing = root / "existing.json"
            existing.write_text("preserve me", encoding="utf-8")
            with self.assertRaisesRegex(MillefeuilleContractError, "already exists"):
                materialize_model_provenance_record_from_files(
                    execution_plan_path=plan_path,
                    execution_evidence_path=evidence_path,
                    output_path=existing,
                )
            self.assertEqual(existing.read_text(encoding="utf-8"), "preserve me")

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "requires no-follow writes")
    def test_model_provenance_output_detects_replacement_race_without_escape(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            evidence_path = root / "evidence.json"
            output_path = root / "provenance.json"
            moved_path = root / "moved-provenance.json"
            outside = root / "outside.json"
            outside.write_text("preserve me", encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            real_write = os.write
            raced = False

            def replace_output_name(fd: int, payload: bytes) -> int:
                nonlocal raced
                if not raced:
                    raced = True
                    output_path.replace(moved_path)
                    output_path.symlink_to(outside)
                return real_write(fd, payload)

            with (
                patch(
                    "millefeuille.domain.secure_io.os.write",
                    side_effect=replace_output_name,
                ),
                self.assertRaisesRegex(MillefeuilleContractError, "changed"),
            ):
                materialize_model_provenance_record_from_files(
                    execution_plan_path=plan_path,
                    execution_evidence_path=evidence_path,
                    output_path=output_path,
                )

            self.assertTrue(raced)
            self.assertEqual(outside.read_text(encoding="utf-8"), "preserve me")
            self.assertTrue(moved_path.is_file())

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "requires no-follow writes")
    def test_model_provenance_output_rejects_run_package_destinations(self):
        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            evidence_path = root / "evidence.json"
            run_package = root / "source-pack" / "analyses" / "millefeuille" / "run-1"
            run_package.mkdir(parents=True)
            (run_package / "stage-manifest.json").write_text("{}", encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            output_path = run_package / "extra" / "provenance.json"

            with self.assertRaisesRegex(MillefeuilleContractError, "run-package"):
                materialize_model_provenance_record_from_files(
                    execution_plan_path=plan_path,
                    execution_evidence_path=evidence_path,
                    output_path=output_path,
                )

            self.assertFalse(output_path.exists())
            self.assertFalse(output_path.parent.exists())

    def test_model_provenance_schemas_validate_generated_records(self):
        root = Path(__file__).resolve().parents[1] / "specs" / "millefeuille-pipeline"
        evidence_schema = json.loads(
            (root / "model-execution-evidence.schema.json").read_text(
                encoding="utf-8"
            )
        )
        record_schema = json.loads(
            (root / "model-provenance-record.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(evidence_schema)
        Draft202012Validator.check_schema(record_schema)

        plan = build_summary_execution_plan(
            profile="research-default",
            stage="summarize_full_paper",
        )
        evidence = self._research_execution_evidence()
        record = materialize_model_provenance_record(
            execution_plan=plan,
            execution_evidence=evidence,
        )

        Draft202012Validator(evidence_schema).validate(evidence)
        Draft202012Validator(record_schema).validate(record)

        malformed = deepcopy(record)
        malformed["prompt"] = "forbidden"
        with self.assertRaises(ValidationError):
            Draft202012Validator(record_schema).validate(malformed)
if __name__ == "__main__":
    unittest.main()
