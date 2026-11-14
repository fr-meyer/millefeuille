"""Synthetic-only semantic binding and pure-planning regressions."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain.acceptance import (
    _build_multi_source_handoff_context,
    historical_versions_match,
)
from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
from millefeuille.domain.classification import _require_passing_acceptance
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import (
    ROLES,
    SCHEMA,
    digest,
    validate_semantic_decision,
)


class SemanticAcceptanceTests(unittest.TestCase):
    def fixture(self, root):
        evidence = []
        for role in sorted(ROLES):
            (root / (role + ".evidence")).write_bytes(role.encode())
            evidence.append(
                {
                    "role": role,
                    "ref": role + ".evidence",
                    "sha256": digest(role.encode()),
                }
            )
        decision = {
            "schema_version": SCHEMA,
            "paper_id": "paper",
            "run_id": "run",
            "source_hash": "hash",
            "reviewer": {
                "identity": "source-reviewer",
                "method": "all-material-claims",
            },
            "outcome": "pass",
            "coverage": "pass",
            "citations": "pass",
            "claims": "pass",
            "versions": "pass",
            "retrieval": "pass",
            "limitations": [],
            "evidence": evidence,
        }
        return decision, evidence

    def validate(self, root, decision, evidence):
        return validate_semantic_decision(
            decision,
            root=root,
            paper_id="paper",
            run_id="run",
            source_hash="hash",
            required_evidence=evidence,
        )

    def test_version_snapshots_reject_missing_malformed_and_drift(self):
        self.assertTrue(historical_versions_match(1, 1))
        for value in (None, True, "1", 1.0, -1, 2, {}, []):
            self.assertFalse(historical_versions_match(value, 1))
            self.assertFalse(historical_versions_match(1, value))

    def test_real_handoff_version_comparison(self):
        source = {
            "identity": {
                "zotero_attachment_key": "ATT",
                "canonical_filename": "paper.pdf",
                "zotero_version": 1,
            },
            "sha256": "hash",
            "byte_size": 12,
        }
        resolved = SimpleNamespace(
            source_pack_manifest={
                "sources": [source],
                "identity": {"zotero_item_key": "ITEM"},
            },
            artifact_index=SimpleNamespace(
                source_identity={"zotero_item_key": "ITEM", "pdf_count": 1}
            ),
            run_dir=Path("/tmp"),
        )
        row = {
            "item_key": "ITEM",
            "attachment_key": "ATT",
            "is_pdf": True,
            "verification_strength": "full",
            "canonical_filename": "paper.pdf",
            "sha256": "hash",
            "file_size_bytes": 12,
            "zotero_version": 1,
        }
        for version in (1, None, "1", True, 2):
            row["zotero_version"] = version
            result = _build_multi_source_handoff_context(
                resolved=resolved,
                rows=[row],
                handoff_path=Path("/tmp/handoff"),
                review_reasons=[],
            )
            self.assertEqual(
                result["details"]["matched"], type(version) is int and version == 1
            )
            self.assertFalse(result["details"]["remote_freshness_verified"])

    def test_review_verdict_identity_inventory_and_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            decision, evidence = self.fixture(root)
            self.validate(root, decision, evidence)
            for field, value in (
                ("outcome", "needs-review"),
                ("coverage", "fail"),
                ("paper_id", "other"),
                ("reviewer", {}),
                ("evidence", evidence[:-1]),
            ):
                changed = copy.deepcopy(decision)
                changed[field] = value
                with self.assertRaises(MillefeuilleContractError):
                    self.validate(root, changed, evidence)
            (root / evidence[0]["ref"]).write_bytes(b"changed")
            with self.assertRaisesRegex(MillefeuilleContractError, "stale"):
                self.validate(root, decision, evidence)

    def test_unsafe_refs_and_no_write_planning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "old").write_bytes(b"before")
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            result = plan_exact_outputs(
                root=root, outputs={"old": b"after", "new/sub": b"new"}
            )
            self.assertEqual(result[1].before_bytes, b"before")
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            for ref in ("../escape", "/absolute", "a/../b", "a\\b", "C:drive", "a//b"):
                with self.assertRaises(MillefeuilleContractError):
                    plan_exact_outputs(root=root, outputs={ref: b"bad"})
            (root / "link").symlink_to(root / "old")
            with self.assertRaises(MillefeuilleContractError):
                plan_exact_outputs(root=root, outputs={"link": b"bad"})

    def test_multisource_classification_cannot_use_structural_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reports").mkdir()
            (root / "reports/acceptance-summary.json").write_text(
                json.dumps({"status": "pass"})
            )
            resolved = SimpleNamespace(
                run_dir=root, source_pack_manifest={"sources": [{}, {}]}
            )
            with self.assertRaises((MillefeuilleContractError, OSError)):
                _require_passing_acceptance(resolved)

    def test_no_write_even_when_later_output_fails_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bad").mkdir()
            with (
                patch("pathlib.Path.write_bytes", side_effect=AssertionError("write")),
                self.assertRaises(MillefeuilleContractError),
            ):
                plan_exact_outputs(root=root, outputs={"aaa": b"new", "bad": b"bad"})
            self.assertFalse((root / "aaa").exists())

    def test_six_output_plan_is_pure_and_binds_manifest_updates(self):
        from millefeuille.domain.acceptance import _build_acceptance_summary
        from millefeuille.domain.semantic_output_plan import plan_semantic_acceptance
        from millefeuille.domain.stage_runtime import resolve_run_artifacts
        from tests.test_millefeuille_stage_cli import (
            PAPER_ID,
            RUN_ID,
            _prepare_fixture_run,
            _write_handoff_jsonl,
        )

        with tempfile.TemporaryDirectory() as temp:
            source_root, run = _prepare_fixture_run(temp)
            resolved = resolve_run_artifacts(
                source_pack_root=source_root, run_id=RUN_ID, paper_id=PAPER_ID
            )
            summary = _build_acceptance_summary(
                resolved=resolved,
                handoff_path=_write_handoff_jsonl(temp),
                duplicate_scan_path=None,
            )
            decision, evidence = self.fixture(run)
            decision.update(
                paper_id=resolved.paper_id,
                run_id=resolved.run_id,
                source_hash=resolved.source_hash,
            )
            from millefeuille.domain.acceptance_output_plan import json_bytes
            from millefeuille.domain.semantic_materials import build_material_binding

            decision["material_binding"] = build_material_binding(resolved)
            decision["structural_sha256"] = digest(json_bytes(summary.to_dict()))
            decision["inventory_sha256"] = digest(
                json.dumps({"evidence": evidence}, sort_keys=True).encode()
            )
            before = {p: p.read_bytes() for p in Path(temp).rglob("*") if p.is_file()}
            plan = plan_semantic_acceptance(
                resolved=resolved,
                summary=summary,
                decision=decision,
                required_evidence=evidence,
            )
            self.assertEqual(len(plan), 6)
            self.assertEqual(
                before,
                {p: p.read_bytes() for p in Path(temp).rglob("*") if p.is_file()},
            )
            rendered = {entry.ref: entry.payload for entry in plan}
            self.assertIn("reports/semantic-decision.json", rendered)
            self.assertIn(
                "reports/semantic-decision.json",
                rendered["stage-manifest.json"].decode(),
            )
            self.assertTrue(
                all(entry.sha256 == digest(entry.payload) for entry in plan)
            )

    def test_status_join_never_promotes_multisource_structural_pass(self):
        from millefeuille.domain.millefeuille import AcceptanceSummaryRecord
        from millefeuille.domain.status_observability import _join_acceptance

        payload = {
            "schema_version": "millefeuille-acceptance-summary/v0.1",
            "paper_id": "paper",
            "run_id": "run",
            "source_hash": "hash",
            "source_pack_ref": "pack",
            "status": "pass",
            "counts": {},
            "checks": [],
        }
        summary = SimpleNamespace(
            paper_id="paper",
            run_id="run",
            source_hash="hash",
            source_pack_ref="pack",
            status=SimpleNamespace(value="pass"),
        )
        context = SimpleNamespace(
            source_pack_dir=None,
            artifact_index=None,
            source_pack_payload={"sources": [{}, {}]},
            run_dir=Path("/nonexistent"),
            paper_id="paper",
            run_id="run",
            source_hash="hash",
        )
        blockers = []
        with (
            patch(
                "millefeuille.domain.status_observability._joined_artifact_record",
                return_value={},
            ),
            patch(
                "millefeuille.domain.status_observability._read_artifact_json",
                return_value=payload,
            ),
            patch(
                "millefeuille.domain.status_observability._validate_acceptance_payload"
            ),
            patch.object(AcceptanceSummaryRecord, "from_dict", return_value=summary),
            patch("millefeuille.domain.status_observability._require_record_identity"),
            patch(
                "millefeuille.domain.status_observability._stage_status",
                return_value="passed",
            ),
        ):
            result = _join_acceptance(context, blockers)
        self.assertEqual(result["status"], "needs-review")
        self.assertIn("acceptance: semantic source review missing or stale", blockers)

    def test_persisted_companion_binds_inventory_and_structural_bytes(self):
        from millefeuille.domain.semantic_acceptance import require_semantic_companion

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reports").mkdir()
            decision, evidence = self.fixture(root)
            structural = b"structural bytes"
            inventory = {"evidence": evidence}
            decision["structural_sha256"] = digest(structural)
            decision["inventory_sha256"] = digest(
                json.dumps(inventory, sort_keys=True).encode()
            )
            (root / "reports/acceptance-summary.json").write_bytes(structural)
            (root / "reports/semantic-review-inventory.json").write_text(
                json.dumps(inventory)
            )
            path = root / "reports/semantic-decision.json"
            path.write_text(json.dumps(decision))
            resolved = SimpleNamespace(
                run_dir=root, paper_id="paper", run_id="run", source_hash="hash"
            )
            with self.assertRaisesRegex(MillefeuilleContractError, "material"):
                require_semantic_companion(resolved)
            (root / "reports/acceptance-summary.json").write_bytes(b"changed")
            with self.assertRaisesRegex(MillefeuilleContractError, "structural"):
                require_semantic_companion(resolved)
            (root / "reports/acceptance-summary.json").write_bytes(structural)
            (root / "reports/semantic-review-inventory.json").write_text(
                json.dumps({"evidence": []})
            )
            with self.assertRaisesRegex(MillefeuilleContractError, "inventory"):
                require_semantic_companion(resolved)
