"""Tests for deterministic offline classification review/adjudication actions."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.artifacts import load_artifact_index
from millefeuille.domain.classification import (
    CLASSIFICATION_ACTION_ADJUDICATION_QUEUE_REF,
    CLASSIFICATION_ACTION_FINAL_DECISION_REF,
    CLASSIFICATION_ACTION_RECORD_REF,
    CLASSIFICATION_ACTION_ROOT_REF,
    CLASSIFICATION_ACTION_TAXONOMY_REQUESTS_REF,
    CLASSIFICATION_PLAN_REF,
)
from millefeuille.domain.stage_runtime import (
    load_jsonl_records,
    load_stage_manifest,
)
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _snapshot_files,
    _write_classification_evidence_json,
    _write_handoff_jsonl,
)

PRIOR_DECISION_REF = (
    f"classification/decision-records/{PAPER_ID}.json"
)
PRIMARY_PATH = "Methods > Normalization & training dynamics"
CORRECTED_PATH = "Methods > Optimization & training dynamics"


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _prepare_classified_run(
    tempdir: str,
    *,
    unresolved: bool = False,
) -> tuple[Path, Path]:
    source_pack_root, run_dir = _prepare_fixture_run(tempdir)
    handoff_path = _write_handoff_jsonl(tempdir)
    acceptance_exit = run_stage_cli(
        [
            "acceptance",
            "--source-pack-root",
            str(source_pack_root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
            "--handoff",
            str(handoff_path),
        ],
        stdout=StringIO(),
    )
    if acceptance_exit != 0:
        raise AssertionError(f"fixture acceptance failed with {acceptance_exit}")
    classification_path = _write_classification_evidence_json(tempdir)
    if unresolved:
        payload = json.loads(classification_path.read_text(encoding="utf-8"))
        payload["review_reasons"] = ["taxonomy fit remains ambiguous"]
        payload["adjudication_required"] = True
        _write_json(classification_path, payload)
    classify_exit = run_stage_cli(
        [
            "classify",
            "--source-pack-root",
            str(source_pack_root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            RUN_ID,
            "--evidence",
            str(classification_path),
        ],
        stdout=StringIO(),
    )
    expected_exit = 2 if unresolved else 0
    if classify_exit != expected_exit:
        raise AssertionError(
            f"fixture classification failed with {classify_exit}, "
            f"expected {expected_exit}"
        )
    return source_pack_root, run_dir


def _write_action_evidence(
    path: Path,
    *,
    action_id: str = "review-001",
    mode: str = "review",
    outcome: str = "no-change",
    primary_path: str = PRIMARY_PATH,
    review_reasons: list[str] | None = None,
    taxonomy_version: str = "taxonomy-v1",
    prior_decision_ref: str = PRIOR_DECISION_REF,
    summary: str = "The prior classification is supported by the reviewed evidence.",
    taxonomy_change_request: dict[str, object] | None = None,
    writeback_preview: dict[str, object] | None = None,
) -> Path:
    payload: dict[str, object] = {
        "schema_version": "millefeuille-classification-action-evidence/v0.1",
        "action_id": action_id,
        "mode": mode,
        "outcome": outcome,
        "taxonomy_version": taxonomy_version,
        "prior_decision_ref": prior_decision_ref,
        "primary_path": primary_path,
        "confidence": "high",
        "summary": summary,
        "evidence_refs": [
            "summaries/hierarchical-summary.json",
            "cards/paper-card.json",
        ],
        "rejected_alternatives": [
            {
                "path": "Applications > Vision",
                "reason": "the primary contribution remains methodological",
            }
        ],
    }
    if review_reasons is not None:
        payload["review_reasons"] = review_reasons
    if taxonomy_change_request is not None:
        payload["taxonomy_change_request"] = taxonomy_change_request
    if writeback_preview is not None:
        payload["writeback_preview"] = writeback_preview
    return _write_json(path, payload)


def _action_args(source_pack_root: Path, action_path: Path) -> list[str]:
    return [
        "classify",
        "--source-pack-root",
        str(source_pack_root),
        "--paper-id",
        PAPER_ID,
        "--run-id",
        RUN_ID,
        "--action-evidence",
        str(action_path),
        "--json",
    ]


class TestMillefeuilleClassificationActions(unittest.TestCase):
    def test_review_no_change_is_idempotent_and_updates_current_refs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir)
            action_path = _write_action_evidence(
                Path(tempdir) / "review.json"
            )
            stdout = StringIO()

            first_exit = run_stage_cli(
                _action_args(root, action_path),
                stdout=stdout,
            )
            first_snapshot = _snapshot_files(root)
            second_exit = run_stage_cli(
                _action_args(root, action_path),
                stdout=StringIO(),
            )
            second_snapshot = _snapshot_files(root)

            self.assertEqual(first_exit, 0)
            self.assertEqual(second_exit, 0)
            self.assertEqual(first_snapshot, second_snapshot)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["outcome"], "no-change")
            self.assertEqual(payload["status"], "classified")
            action_dir = run_dir / CLASSIFICATION_ACTION_ROOT_REF / "review-001"
            record = json.loads(
                (action_dir / CLASSIFICATION_ACTION_RECORD_REF).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(record["prior_decision_ref"], PRIOR_DECISION_REF)
            self.assertEqual(record["mode"], "review")
            plan = json.loads(
                (run_dir / CLASSIFICATION_PLAN_REF).read_text(encoding="utf-8")
            )
            self.assertEqual(plan["mode"], "review")
            self.assertEqual(
                plan["papers"][0]["decision_ref"],
                "classification/actions/review-001/final-decision.json",
            )
            artifact_index = load_artifact_index(run_dir / "artifact-index.json")
            self.assertIn("classification_action", artifact_index.artifacts)
            self.assertEqual(
                artifact_index.artifacts["classification_decision"]["ref"],
                "classification/actions/review-001/final-decision.json",
            )

    def test_review_correction_materializes_final_decision_and_writeback(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir)
            action_path = _write_action_evidence(
                Path(tempdir) / "review-correction.json",
                action_id="review-correction-001",
                outcome="corrected",
                primary_path=CORRECTED_PATH,
                summary="The full-method evidence supports the corrected path.",
                writeback_preview={
                    "add_tags": ["millefeuille-classified"],
                    "remove_tags": ["millefeuille"],
                    "destination_collection": CORRECTED_PATH,
                },
            )

            exit_code = run_stage_cli(
                _action_args(root, action_path),
                stdout=StringIO(),
            )

            self.assertEqual(exit_code, 0)
            action_dir = (
                run_dir
                / CLASSIFICATION_ACTION_ROOT_REF
                / "review-correction-001"
            )
            final_decision = json.loads(
                (action_dir / CLASSIFICATION_ACTION_FINAL_DECISION_REF).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(final_decision["primary_path"], CORRECTED_PATH)
            self.assertEqual(final_decision["status"], "classified")
            writeback_exit = run_stage_cli(
                [
                    "writeback",
                    "--source-pack-root",
                    str(root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                ],
                stdout=StringIO(),
            )
            self.assertEqual(writeback_exit, 0)

    def test_review_escalation_routes_to_adjudication_without_classified_tag(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir)
            action_path = _write_action_evidence(
                Path(tempdir) / "review-escalation.json",
                action_id="review-escalation-001",
                outcome="escalated",
                review_reasons=["two taxonomy clauses remain equally supported"],
            )

            exit_code = run_stage_cli(
                _action_args(root, action_path),
                stdout=StringIO(),
            )

            self.assertEqual(exit_code, 2)
            action_dir = (
                run_dir
                / CLASSIFICATION_ACTION_ROOT_REF
                / "review-escalation-001"
            )
            queue = load_jsonl_records(
                action_dir / CLASSIFICATION_ACTION_ADJUDICATION_QUEUE_REF,
                "adjudication queue",
            )
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0]["status"], "adjudication-required")
            preview = json.loads(
                (
                    run_dir / "classification/zotero-writeback-preview.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                preview["add_tags"],
                ["millefeuille-classification-review"],
            )
            stage_manifest = load_stage_manifest(run_dir / "stage-manifest.json")
            stage_map = {
                stage.name.value: stage.status.value
                for stage in stage_manifest.stages
            }
            self.assertEqual(stage_map["classify"], "needs-review")

    def test_adjudication_confirmation_resolves_unresolved_prior(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir, unresolved=True)
            action_path = _write_action_evidence(
                Path(tempdir) / "adjudication.json",
                action_id="adjudication-001",
                mode="adjudicate",
                outcome="confirmed",
                summary="The coordinator confirmed the evidence-backed path.",
            )

            exit_code = run_stage_cli(
                _action_args(root, action_path),
                stdout=StringIO(),
            )

            self.assertEqual(exit_code, 0)
            action_dir = (
                run_dir / CLASSIFICATION_ACTION_ROOT_REF / "adjudication-001"
            )
            final_decision = json.loads(
                (action_dir / CLASSIFICATION_ACTION_FINAL_DECISION_REF).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(final_decision["mode"], "adjudicate")
            self.assertEqual(final_decision["status"], "classified")

    def test_chained_adjudication_preserves_historical_action_preview(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir)
            review_path = _write_action_evidence(
                Path(tempdir) / "review-chain.json",
                action_id="review-chain",
                outcome="escalated",
                review_reasons=["coordinator adjudication is required"],
            )
            review_exit = run_stage_cli(
                _action_args(root, review_path),
                stdout=StringIO(),
            )
            review_dir = (
                run_dir / CLASSIFICATION_ACTION_ROOT_REF / "review-chain"
            )
            review_snapshot = {
                path.relative_to(review_dir).as_posix(): path.read_bytes()
                for path in review_dir.rglob("*")
                if path.is_file()
            }
            review_record = json.loads(
                (review_dir / CLASSIFICATION_ACTION_RECORD_REF).read_text(
                    encoding="utf-8"
                )
            )
            review_decision = json.loads(
                (review_dir / CLASSIFICATION_ACTION_FINAL_DECISION_REF).read_text(
                    encoding="utf-8"
                )
            )
            scoped_preview_ref = (
                "classification/actions/review-chain/"
                "zotero-writeback-preview.json"
            )

            adjudication_path = _write_action_evidence(
                Path(tempdir) / "adjudication-chain.json",
                action_id="adjudication-chain",
                mode="adjudicate",
                outcome="confirmed",
                prior_decision_ref=(
                    "classification/actions/review-chain/final-decision.json"
                ),
            )
            adjudication_exit = run_stage_cli(
                _action_args(root, adjudication_path),
                stdout=StringIO(),
            )
            current_plan = json.loads(
                (run_dir / CLASSIFICATION_PLAN_REF).read_text(encoding="utf-8")
            )

            self.assertEqual(review_exit, 2)
            self.assertEqual(adjudication_exit, 0)
            self.assertEqual(
                review_record["writeback_preview_ref"],
                scoped_preview_ref,
            )
            self.assertEqual(
                review_decision["writeback_preview_ref"],
                scoped_preview_ref,
            )
            self.assertEqual(
                {
                    path.relative_to(review_dir).as_posix(): path.read_bytes()
                    for path in review_dir.rglob("*")
                    if path.is_file()
                },
                review_snapshot,
            )
            self.assertEqual(
                current_plan["papers"][0]["decision_ref"],
                "classification/actions/adjudication-chain/final-decision.json",
            )

    def test_taxonomy_change_request_remains_unresolved_and_is_recorded(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir, unresolved=True)
            action_path = _write_action_evidence(
                Path(tempdir) / "taxonomy-request.json",
                action_id="adjudication-taxonomy-001",
                mode="adjudicate",
                outcome="taxonomy-change-requested",
                review_reasons=["the locked taxonomy has no clean primary fit"],
                summary="The gap requires taxonomy-lead review.",
                taxonomy_change_request={
                    "proposed_path": "Methods > Representation diagnostics",
                    "reason": "the contribution does not fit an existing leaf",
                    "evidence_refs": ["summaries/hierarchical-summary.json"],
                },
            )

            exit_code = run_stage_cli(
                _action_args(root, action_path),
                stdout=StringIO(),
            )

            self.assertEqual(exit_code, 2)
            action_dir = (
                run_dir
                / CLASSIFICATION_ACTION_ROOT_REF
                / "adjudication-taxonomy-001"
            )
            requests = load_jsonl_records(
                action_dir / CLASSIFICATION_ACTION_TAXONOMY_REQUESTS_REF,
                "taxonomy change requests",
            )
            self.assertEqual(len(requests), 1)
            self.assertEqual(
                requests[0]["proposed_path"],
                "Methods > Representation diagnostics",
            )

    def test_adjudication_rejects_already_classified_prior_before_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir)
            action_path = _write_action_evidence(
                Path(tempdir) / "invalid-adjudication.json",
                action_id="invalid-adjudication",
                mode="adjudicate",
                outcome="confirmed",
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _action_args(root, action_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("adjudication requires", stderr.getvalue())
            self.assertFalse(
                (
                    run_dir
                    / CLASSIFICATION_ACTION_ROOT_REF
                    / "invalid-adjudication"
                ).exists()
            )

    def test_traversal_and_taxonomy_drift_fail_before_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir)
            traversal_path = _write_action_evidence(
                Path(tempdir) / "traversal.json",
                action_id="traversal-action",
                prior_decision_ref="../decision.json",
            )
            drift_path = _write_action_evidence(
                Path(tempdir) / "drift.json",
                action_id="taxonomy-drift",
                taxonomy_version="taxonomy-v2",
            )
            nested_traversal_path = _write_action_evidence(
                Path(tempdir) / "nested-traversal.json",
                action_id="nested-traversal-action",
            )
            nested_traversal_payload = json.loads(
                nested_traversal_path.read_text(encoding="utf-8")
            )
            nested_traversal_payload["rejected_alternatives"][0][
                "evidence_refs"
            ] = ["../outside.json"]
            _write_json(nested_traversal_path, nested_traversal_payload)

            traversal_exit = run_stage_cli(
                _action_args(root, traversal_path),
                stderr=StringIO(),
            )
            drift_exit = run_stage_cli(
                _action_args(root, drift_path),
                stderr=StringIO(),
            )
            nested_traversal_exit = run_stage_cli(
                _action_args(root, nested_traversal_path),
                stderr=StringIO(),
            )

            self.assertEqual(traversal_exit, 2)
            self.assertEqual(drift_exit, 2)
            self.assertEqual(nested_traversal_exit, 2)
            self.assertFalse(
                (run_dir / CLASSIFICATION_ACTION_ROOT_REF).exists()
            )

    def test_unknown_fields_and_invalid_types_fail_before_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir)
            unknown_path = _write_action_evidence(
                Path(tempdir) / "unknown-field.json",
                action_id="unknown-field",
            )
            unknown_payload = json.loads(
                unknown_path.read_text(encoding="utf-8")
            )
            unknown_payload["unexpected"] = True
            _write_json(unknown_path, unknown_payload)
            invalid_type_path = _write_action_evidence(
                Path(tempdir) / "invalid-type.json",
                action_id="invalid-type",
            )
            invalid_type_payload = json.loads(
                invalid_type_path.read_text(encoding="utf-8")
            )
            invalid_type_payload["action_id"] = 7
            _write_json(invalid_type_path, invalid_type_payload)
            nested_invalid_path = _write_action_evidence(
                Path(tempdir) / "nested-invalid-type.json",
                action_id="nested-invalid-type",
            )
            nested_invalid_payload = json.loads(
                nested_invalid_path.read_text(encoding="utf-8")
            )
            nested_invalid_payload["rejected_alternatives"][0]["path"] = 7
            _write_json(nested_invalid_path, nested_invalid_payload)
            unknown_stderr = StringIO()
            invalid_type_stderr = StringIO()
            nested_invalid_stderr = StringIO()

            unknown_exit = run_stage_cli(
                _action_args(root, unknown_path),
                stderr=unknown_stderr,
            )
            invalid_type_exit = run_stage_cli(
                _action_args(root, invalid_type_path),
                stderr=invalid_type_stderr,
            )
            nested_invalid_exit = run_stage_cli(
                _action_args(root, nested_invalid_path),
                stderr=nested_invalid_stderr,
            )

            self.assertEqual(unknown_exit, 2)
            self.assertEqual(invalid_type_exit, 2)
            self.assertEqual(nested_invalid_exit, 2)
            self.assertIn("unsupported fields", unknown_stderr.getvalue())
            self.assertIn("must be a non-empty string", invalid_type_stderr.getvalue())
            self.assertIn(
                "must be a non-empty string",
                nested_invalid_stderr.getvalue(),
            )
            self.assertFalse(
                (run_dir / CLASSIFICATION_ACTION_ROOT_REF).exists()
            )

    def test_existing_action_id_rejects_drift_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_classified_run(tempdir)
            action_path = _write_action_evidence(
                Path(tempdir) / "review.json",
                action_id="immutable-review",
            )
            first_exit = run_stage_cli(
                _action_args(root, action_path),
                stdout=StringIO(),
            )
            record_path = (
                run_dir
                / CLASSIFICATION_ACTION_ROOT_REF
                / "immutable-review"
                / CLASSIFICATION_ACTION_RECORD_REF
            )
            original = record_path.read_bytes()
            _write_action_evidence(
                action_path,
                action_id="immutable-review",
                summary="A changed summary must not overwrite the action.",
            )
            stderr = StringIO()

            second_exit = run_stage_cli(
                _action_args(root, action_path),
                stderr=stderr,
            )

            self.assertEqual(first_exit, 0)
            self.assertEqual(second_exit, 2)
            self.assertIn("already exists with drift", stderr.getvalue())
            self.assertEqual(record_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
