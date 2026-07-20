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
)
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE


class ModelExecutionPlanTests(unittest.TestCase):
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
                "summaries/request/full-paper.json",
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
        self.assertNotIn("fallback_used", record)
        self.assertNotIn("authentication", record)
        self.assertNotIn("execution", record)

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
            ("output_refs", ["../leak.json"], "safe relative"),
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
            ("reasoning_effort", "xhigh "),
            (
                "input_refs",
                [
                    " structure/structure.json",
                    "summaries/request/full-paper.json",
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
