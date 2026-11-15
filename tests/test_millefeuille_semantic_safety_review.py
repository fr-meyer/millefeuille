"""Regressions for independently reproduced semantic/planner false passes."""

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain.acceptance import _build_acceptance_summary
from millefeuille.domain.acceptance_output_plan import (
    PlanningLimits,
    plan_exact_outputs,
)
from millefeuille.domain.classification import _require_passing_acceptance
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest, require_semantic_companion
from millefeuille.domain.semantic_materials import build_material_binding
from millefeuille.domain.semantic_output_plan import plan_semantic_acceptance
from millefeuille.domain.stage_runtime import resolve_run_artifacts
import tests.test_millefeuille_semantic_acceptance as semantic_fixtures
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)


class ReviewSafetyTests(unittest.TestCase):
    def prepared(self, temp):
        root, run = _prepare_fixture_run(temp)
        resolved = resolve_run_artifacts(
            source_pack_root=root, run_id=RUN_ID, paper_id=PAPER_ID
        )
        source = dict(resolved.source_pack_manifest["source"])
        source["identity"] = dict(resolved.source_pack_manifest["identity"])
        second = copy.deepcopy(source)
        second["ref"] = "supplement.pdf"
        second["identity"]["zotero_attachment_key"] = "ATT2"
        (resolved.source_pack_dir / second["ref"]).write_bytes(
            (resolved.source_pack_dir / source["ref"]).read_bytes()
        )
        manifest = dict(resolved.source_pack_manifest, sources=[source, second])
        resolved = replace(resolved, source_pack_manifest=manifest)
        decision, evidence = semantic_fixtures.SemanticAcceptanceTests().fixture(run)
        decision.update(
            paper_id=PAPER_ID, run_id=RUN_ID, source_hash=resolved.source_hash
        )
        decision["material_binding"] = build_material_binding(resolved)
        structural = json.dumps(
            {
                "status": "pass",
                "paper_id": PAPER_ID,
                "run_id": RUN_ID,
                "source_hash": resolved.source_hash,
            }
        ).encode()
        inventory = {"evidence": evidence}
        decision["inventory_sha256"] = digest(
            json.dumps(inventory, sort_keys=True).encode()
        )
        decision["structural_sha256"] = digest(structural)
        (run / "reports").mkdir(exist_ok=True)
        (run / "reports/acceptance-summary.json").write_bytes(structural)
        (run / "reports/semantic-review-inventory.json").write_text(
            json.dumps(inventory)
        )
        (run / "reports/semantic-decision.json").write_text(json.dumps(decision))
        return resolved, decision, evidence

    def test_actual_multisource_binding_passes_then_omission_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            resolved, decision, _ = self.prepared(temp)
            require_semantic_companion(resolved)
            _require_passing_acceptance(resolved)
            decision["material_binding"]["files"] = [
                x
                for x in decision["material_binding"]["files"]
                if x["name"] != "source:ATT2"
            ]
            (resolved.run_dir / "reports/semantic-decision.json").write_text(
                json.dumps(decision)
            )
            with self.assertRaisesRegex(MillefeuilleContractError, "material binding"):
                _require_passing_acceptance(resolved)

    def test_missing_actual_source_cannot_be_replaced_by_role_placeholders(self):
        with tempfile.TemporaryDirectory() as temp:
            resolved, _, _ = self.prepared(temp)
            (resolved.source_pack_dir / "supplement.pdf").unlink()
            with self.assertRaises((MillefeuilleContractError, OSError)):
                _require_passing_acceptance(resolved)

    def test_card_summary_text_hierarchy_index_and_attachment_changes_invalidate(self):
        paths = [
            "cards/paper-card.json",
            "cards/paper-card.md",
            "summaries/texts/full-paper.md",
            "summaries/hierarchical-summary.json",
            "index/index-status.json",
        ]
        for ref in paths:
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as temp:
                resolved, _, _ = self.prepared(temp)
                (resolved.run_dir / ref).write_bytes(b"changed actual material")
                with self.assertRaises((MillefeuilleContractError, OSError)):
                    _require_passing_acceptance(resolved)
        with tempfile.TemporaryDirectory() as temp:
            resolved, _, _ = self.prepared(temp)
            resolved.source_pack_manifest["sources"][1]["identity"][
                "zotero_attachment_key"
            ] = "WRONG"
            with self.assertRaises(MillefeuilleContractError):
                _require_passing_acceptance(resolved)

    def test_wrong_hash_escape_and_symlink_actual_material_refuse(self):
        for change in ("hash", "escape", "symlink"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                resolved, _, _ = self.prepared(temp)
                if change == "hash":
                    resolved.source_pack_manifest["sources"][1]["sha256"] = "0" * 64
                elif change == "escape":
                    resolved.artifact_index.artifacts["paper_card_json"]["ref"] = (
                        "../../../../../../outside"
                    )
                else:
                    path = resolved.run_dir / "cards/paper-card.md"
                    path.unlink()
                    path.symlink_to(resolved.run_dir / "cards/paper-card.json")
                with self.assertRaises((MillefeuilleContractError, OSError)):
                    build_material_binding(resolved)

    def test_output_ancestor_collision_and_payload_bounds_precede_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for outputs in (
                {"file": b"a", "file/child": b"b"},
                {"big": b"x" * (2 * 1024 * 1024 + 1)},
                {str(i): b"x" for i in range(65)},
            ):
                with (
                    patch(
                        "millefeuille.domain.acceptance_output_plan.read_bytes_no_follow",
                        side_effect=AssertionError("unexpected read"),
                    ),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    plan_exact_outputs(root=root, outputs=outputs)
            self.assertEqual(list(root.iterdir()), [])

    def test_limit_exactly_and_one_over_invalid_bool_and_aggregate_before(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            limits = PlanningLimits(
                max_files=2,
                max_file_bytes=2,
                max_output_bytes=2,
                max_before_file_bytes=2,
                max_before_bytes=2,
                max_combined_bytes=4,
            )
            (root / "a").write_bytes(b"12")
            self.assertEqual(
                len(plan_exact_outputs(root=root, outputs={"a": b"34"}, limits=limits)),
                1,
            )
            (root / "b").write_bytes(b"1")
            with self.assertRaises(MillefeuilleContractError):
                plan_exact_outputs(
                    root=root, outputs={"a": b"3", "b": b"4"}, limits=limits
                )
            for value in (True, 0, -1, 1.0, None, 65):
                with (
                    self.subTest(value=value),
                    self.assertRaises(MillefeuilleContractError),
                ):
                    plan_exact_outputs(
                        root=root,
                        outputs={"new": b"1"},
                        limits=replace(limits, max_files=value),
                    )
            with self.assertRaises(MillefeuilleContractError):
                plan_exact_outputs(root=root, outputs={"new": b"123"}, limits=limits)

    def test_semantic_six_roles_cannot_collapse_via_resolved_output_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            root, run = _prepare_fixture_run(temp)
            resolved = resolve_run_artifacts(
                source_pack_root=root, run_id=RUN_ID, paper_id=PAPER_ID
            )
            summary = _build_acceptance_summary(
                resolved=resolved,
                handoff_path=_write_handoff_jsonl(temp),
                duplicate_scan_path=None,
            )
            decision, evidence = semantic_fixtures.SemanticAcceptanceTests().fixture(
                run
            )
            from millefeuille.domain.acceptance_output_plan import json_bytes

            decision.update(
                paper_id=PAPER_ID,
                run_id=RUN_ID,
                source_hash=resolved.source_hash,
                material_binding=build_material_binding(resolved),
            )
            decision["inventory_sha256"] = digest(
                json.dumps({"evidence": evidence}, sort_keys=True).encode()
            )
            decision["structural_sha256"] = digest(json_bytes(summary.to_dict()))
            for target in (
                run / "reports/semantic-decision.json",
                resolved.artifact_index_path,
            ):
                alias = replace(resolved, stage_manifest_path=target)
                with self.assertRaisesRegex(MillefeuilleContractError, "overlapping"):
                    plan_semantic_acceptance(
                        resolved=alias,
                        summary=summary,
                        decision=decision,
                        required_evidence=evidence,
                    )
