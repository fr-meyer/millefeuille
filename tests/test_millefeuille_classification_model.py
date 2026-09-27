"""Accepted-run, taxonomy, source drift and output routing regressions."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.acceptance import write_acceptance_summary
from millefeuille.domain.classification_model import (
    OUTPUT_SCHEMA,
    prepare_classification_model_plan,
    validate_classification_model_output,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import (
    build_model_executor_request,
    validate_model_executor_request,
    verify_model_executor_input,
)
from millefeuille.domain.summary_fixtures import load_hierarchical_summary
from millefeuille.domain.taxonomy import create_taxonomy_lock, seal_taxonomy_registry
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)


class ClassificationModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root, self.run = _prepare_fixture_run(self.temp.name)
        self.handoff = self.run / "reports/model-classification-handoff.jsonl"
        self.handoff.parent.mkdir(exist_ok=True)
        self.handoff.write_bytes(_write_handoff_jsonl(self.temp.name).read_bytes())
        write_acceptance_summary(
            source_pack_root=self.root,
            run_id=RUN_ID,
            paper_id=PAPER_ID,
            handoff_path=self.handoff,
        )
        self.lock_path = self.run / "taxonomy-lock.json"
        self.registry = self.make_registry()
        self.write_lock(self.registry)
        selected = self.root / "zotero" / PAPER_ID / "selected/fulltext.md"
        self.selected_sha = (
            "sha256:" + hashlib.sha256(selected.read_bytes()).hexdigest()
        )

    @staticmethod
    def make_registry():
        entries = []
        for entry_id, level, parent, label in (
            ("L1-METHODS", 1, None, "Methods"),
            ("L2-TRAINING", 2, "L1-METHODS", "Training"),
            ("L2-ARCHITECTURE", 2, "L1-METHODS", "Architecture"),
        ):
            entries.append(
                {
                    "entry_id": entry_id,
                    "level": level,
                    "parent_id": parent,
                    "label": label,
                    "definition": "Synthetic " + label + " definition.",
                    "include_when": [
                        "Primary contribution fits this synthetic definition."
                    ],
                    "exclude_when": ["Primary contribution fits another definition."],
                    "boundary_notes": [],
                    "status": "active",
                    "replacement_id": None,
                }
            )
        return seal_taxonomy_registry(
            {
                "schema_version": "millefeuille-taxonomy-registry/v0.1",
                "registry_id": "synthetic",
                "taxonomy_version": "synthetic-v1",
                "previous_version": None,
                "previous_content_identity": None,
                "status": "released",
                "governing_basis": "Primary intellectual contribution",
                "owner_id": "synthetic-owner",
                "entries": entries,
            }
        )

    def write_lock(self, registry, scope_id=RUN_ID):
        lock = create_taxonomy_lock(
            registry,
            lock_id="synthetic-lock",
            scope_type="single-run",
            scope_id=scope_id,
            locked_by="synthetic-operator",
            locked_at="2026-09-27T00:00:00Z",
        )
        self.lock_path.write_text(json.dumps(lock))
        return lock

    def plan(self):
        return prepare_classification_model_plan(
            source_pack_root=self.root,
            run_id=RUN_ID,
            paper_id=PAPER_ID,
            expected_selected_text_sha256=self.selected_sha,
            taxonomy_lock_path=self.lock_path,
            handoff_path=self.handoff,
        )

    def output(self, plan):
        return {
            "schema_version": OUTPUT_SCHEMA,
            "paper_id": PAPER_ID,
            "run_id": RUN_ID,
            "source_hash": plan.source_hash,
            "taxonomy_version": plan.taxonomy_lock["taxonomy_version"],
            "taxonomy_lock_identity": plan.taxonomy_lock["content_identity"],
            "primary_entry_id": "L2-TRAINING",
            "strongest_rejected_entry_id": "L2-ARCHITECTURE",
            "confidence": "high",
            "rationale": "Synthetic paper evidence concerns training.",
            "evidence": [
                {"locator": plan.source_locators[0], "claim": "Synthetic evidence."}
            ],
            "taxonomy_gap": False,
            "review_reasons": [],
        }

    def test_actual_accepted_run_freezes_private_evidence_without_effects(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        plan = self.plan()
        packet = json.loads(plan.prompt)
        verify_model_executor_input(plan.request, plan.prompt)
        self.assertEqual(plan.request["task"]["kind"], "classify")
        self.assertEqual(plan.request["requested_model"], "openai/gpt-5.6-sol")
        self.assertEqual(plan.request["thinking"], "xhigh")
        self.assertEqual(plan.request["retry"]["max_attempts"], 1)
        self.assertEqual(plan.request["fallback"]["models"], [])
        self.assertIn("Selected text", packet["selected_fulltext"])
        self.assertIn("Full paper summary", packet["full_paper_summary"])
        self.assertNotIn("selected_fulltext", plan.to_dict())
        self.assertEqual(
            before,
            {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()},
        )
        self.assertEqual(
            validate_classification_model_output(self.output(plan), plan=plan)[
                "primary_entry_id"
            ],
            "L2-TRAINING",
        )

    def test_exported_metadata_and_executor_snapshot_do_not_alias_plan(self):
        plan = self.plan()
        before = deepcopy(plan.request)
        exported = plan.to_dict()
        exported["request"]["timeout_seconds"] = 1
        exported["input_files"][0]["sha256"] = "changed"
        snapshot = plan.executor_request()
        snapshot["retry"]["max_attempts"] = 2
        self.assertEqual(plan.request, before)
        self.assertNotEqual(plan.input_files[0]["sha256"], "changed")
        self.assertEqual(plan.executor_request(), before)
        validate_classification_model_output(self.output(plan), plan=plan)

    def test_generically_valid_rebound_request_mutations_are_rejected(self):
        for changes in (
            {"requested_model": "xai/grok-4.6"},
            {"timeout_seconds": 300},
            {"prompt_template_id": "another-template"},
            {"prompt_template_version": "0.2"},
            {"output_schema_id": "another-output"},
            {"output_schema_version": "0.2"},
            {"unit_id": "another-unit"},
            {"source_locators": ["p.999"]},
            {"task_kind": "paper_card"},
            {
                "max_attempts": 2,
                "retry_on": ["timeout"],
                "fallback_models": ["xai/grok-4.6"],
            },
        ):
            with self.subTest(changes=changes):
                plan = self.plan()
                args = {
                    "task_kind": "classify",
                    "unit_id": "classification",
                    "source_locators": list(plan.source_locators),
                    "requested_model": "openai/gpt-5.6-sol",
                    "thinking": "xhigh",
                    "prompt_template_id": "millefeuille-classification",
                    "prompt_template_version": "0.1",
                    "input_payload": plan.prompt,
                    "output_schema_id": "millefeuille-classification-model-output",
                    "output_schema_version": "0.1",
                    "timeout_seconds": 600,
                    "max_attempts": 1,
                    "retry_on": [],
                    "fallback_models": [],
                }
                args.update(changes)
                rebound = build_model_executor_request(**args)
                validate_model_executor_request(rebound)
                verify_model_executor_input(rebound, plan.prompt)
                plan.request.clear()
                plan.request.update(rebound)
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "request commitment drift"
                ):
                    plan.executor_request()
                with self.assertRaisesRegex(
                    MillefeuilleContractError, "request commitment drift"
                ):
                    validate_classification_model_output(self.output(plan), plan=plan)

    def test_missing_or_failed_saved_acceptance_rejects(self):
        path = self.run / "reports/acceptance-summary.json"
        payload = json.loads(path.read_text())
        payload["status"] = "needs-review"
        path.write_text(json.dumps(payload))
        with self.assertRaises(MillefeuilleContractError):
            self.plan()

    def test_passing_cached_acceptance_cannot_hide_selected_text_drift(self):
        selected = self.root / "zotero" / PAPER_ID / "selected/fulltext.md"
        selected.write_text("Changed source")
        with self.assertRaises(MillefeuilleContractError):
            self.plan()

    def test_taxonomy_scope_drift_rejects(self):
        self.write_lock(self.registry, scope_id="another-run")
        with self.assertRaises(MillefeuilleContractError):
            self.plan()

    def test_draft_taxonomy_cannot_be_locked(self):
        draft = deepcopy(self.registry)
        draft.pop("content_identity")
        draft["status"] = "draft"
        sealed = seal_taxonomy_registry(draft)
        with self.assertRaises(MillefeuilleContractError):
            self.write_lock(sealed)

    def test_symlinked_taxonomy_is_rejected(self):
        external = Path(self.temp.name) / "external-lock.json"
        self.lock_path.rename(external)
        try:
            self.lock_path.symlink_to(external)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaises(MillefeuilleContractError):
            self.plan()

    def test_symlinked_summary_inside_corpus_is_rejected(self):
        view = load_hierarchical_summary(
            self.run / "summaries/hierarchical-summary.json"
        )
        unit = next(u for u in view["summaries"] if u["grain"] == "full-paper")
        path = self.run / "summaries" / unit["text_ref"]
        destination = path.with_name("original-summary.md")
        path.rename(destination)
        try:
            path.symlink_to(destination)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaises(MillefeuilleContractError):
            self.plan()

    def test_output_identity_and_locked_taxonomy_drift_rejects(self):
        plan = self.plan()
        for field in (
            "paper_id",
            "run_id",
            "source_hash",
            "taxonomy_version",
            "taxonomy_lock_identity",
        ):
            with self.subTest(field=field):
                output = self.output(plan)
                output[field] = "wrong"
                with self.assertRaises(MillefeuilleContractError):
                    validate_classification_model_output(output, plan=plan)

    def test_unknown_parent_retired_and_self_alternative_nodes_reject(self):
        plan = self.plan()
        for field, value in (
            ("primary_entry_id", "unknown"),
            ("primary_entry_id", "L1-METHODS"),
            ("strongest_rejected_entry_id", "L2-TRAINING"),
            ("strongest_rejected_entry_id", "unknown"),
        ):
            with self.subTest(field=field, value=value):
                output = self.output(plan)
                output[field] = value
                with self.assertRaises(MillefeuilleContractError):
                    validate_classification_model_output(output, plan=plan)

    def test_retired_alternative_cannot_be_selected(self):
        registry = deepcopy(self.registry)
        registry.pop("content_identity")
        next(e for e in registry["entries"] if e["entry_id"] == "L2-ARCHITECTURE")[
            "status"
        ] = "deprecated"
        self.write_lock(seal_taxonomy_registry(registry))
        plan = self.plan()
        with self.assertRaises(MillefeuilleContractError):
            validate_classification_model_output(self.output(plan), plan=plan)

    def test_missing_snapshot_binding_rejects_before_planning(self):
        self.selected_sha = ""
        with self.assertRaises(MillefeuilleContractError):
            self.plan()

    def test_page_outside_paper_and_missing_evidence_reject(self):
        plan = self.plan()
        for evidence in (
            [],
            [{"locator": "p.999", "claim": "Unknown page"}],
            [{"locator": plan.source_locators[0], "claim": ""}],
        ):
            output = self.output(plan)
            output["evidence"] = evidence
            with self.assertRaises(MillefeuilleContractError):
                validate_classification_model_output(output, plan=plan)

    def test_uncertainty_requires_explicit_review(self):
        plan = self.plan()
        for field, value in (("confidence", "low"), ("taxonomy_gap", True)):
            output = self.output(plan)
            output[field] = value
            with self.assertRaises(MillefeuilleContractError):
                validate_classification_model_output(output, plan=plan)
            output["review_reasons"] = ["Synthetic uncertainty"]
            validate_classification_model_output(output, plan=plan)

    def test_mutated_plan_is_rejected_even_with_resealed_taxonomy(self):
        plan = self.plan()
        changed = deepcopy(self.registry)
        changed.pop("content_identity")
        changed["entries"][1]["definition"] = "Changed meaning"
        changed = seal_taxonomy_registry(changed)
        replacement = self.write_lock(changed)
        plan.taxonomy_lock.clear()
        plan.taxonomy_lock.update(replacement)
        with self.assertRaises(MillefeuilleContractError):
            validate_classification_model_output(self.output(plan), plan=plan)

    def test_unknown_output_fields_and_nonboolean_gap_reject(self):
        plan = self.plan()
        output = self.output(plan)
        output["unexpected"] = True
        with self.assertRaises(MillefeuilleContractError):
            validate_classification_model_output(output, plan=plan)
        output = self.output(plan)
        output["taxonomy_gap"] = "false"
        with self.assertRaises(MillefeuilleContractError):
            validate_classification_model_output(output, plan=plan)


if __name__ == "__main__":
    unittest.main()
