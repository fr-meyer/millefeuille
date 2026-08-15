"""Tests for deterministic offline batch classification routing."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.card_index_contract import canonical_json_bytes
from millefeuille.domain.classification import (
    CLASSIFICATION_BATCH_REPORT_REF,
    CLASSIFICATION_BATCH_ROOT_REF,
    CLASSIFICATION_BATCH_SUMMARY_REF,
    CLASSIFICATION_PLAN_REF,
)
from millefeuille.domain.index_fixtures import INDEX_STATUS_REF
from millefeuille.domain.summary_fixtures import SUMMARY_ARTIFACT_REF
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _clone_fixture_run(run_dir: Path, run_id: str) -> Path:
    destination = run_dir.parent / run_id
    shutil.copytree(run_dir, destination)
    for relative_path in (
        Path("stage-manifest.json"),
        Path("artifact-index.json"),
        SUMMARY_ARTIFACT_REF,
        Path("cards/paper-card.json"),
        INDEX_STATUS_REF,
    ):
        target = destination / relative_path
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["run_id"] = run_id
        _write_json(target, payload)
    index_path = destination / "artifact-index.json"
    index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    index_payload["artifact_root"] = str(destination)
    _write_json(index_path, index_payload)
    return destination


def _accept_run(
    *,
    source_pack_root: Path,
    run_id: str,
    handoff_path: Path,
) -> None:
    exit_code = run_stage_cli(
        [
            "acceptance",
            "--source-pack-root",
            str(source_pack_root),
            "--paper-id",
            PAPER_ID,
            "--run-id",
            run_id,
            "--handoff",
            str(handoff_path),
        ],
        stdout=StringIO(),
    )
    if exit_code != 0:
        raise AssertionError(f"fixture acceptance failed with {exit_code}")


def _write_classification_evidence(
    path: Path,
    *,
    taxonomy_version: str = "taxonomy-v1",
    primary_path: str = "Methods > Training",
    review_reasons: list[str] | None = None,
    adjudication_required: bool = False,
    mode: str = "batch",
) -> None:
    payload: dict[str, object] = {
        "schema_version": "millefeuille-classification-fixture-evidence/v0.1",
        "taxonomy_version": taxonomy_version,
        "mode": mode,
        "primary_path": primary_path,
        "confidence": "high",
        "evidence_refs": [
            "summaries/hierarchical-summary.json",
            "cards/paper-card.json",
        ],
        "rejected_alternatives": [],
    }
    if review_reasons:
        payload["review_reasons"] = review_reasons
    if adjudication_required:
        payload["adjudication_required"] = True
    _write_json(path, payload)


def _write_batch_manifest(
    path: Path,
    *,
    batch_id: object = "classification-fixture",
    taxonomy_version: object = "taxonomy-v1",
    runs: list[dict[str, object]],
) -> None:
    _write_json(
        path,
        {
            "schema_version": "millefeuille-classification-batch-manifest/v0.1",
            "batch_id": batch_id,
            "taxonomy_version": taxonomy_version,
            "runs": runs,
        },
    )


def _batch_args(*, source_pack_root: Path, manifest_path: Path) -> list[str]:
    return [
        "classify",
        "--source-pack-root",
        str(source_pack_root),
        "--batch-manifest",
        str(manifest_path),
        "--json",
    ]


def _snapshot_classification_outputs(source_pack_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(source_pack_root).as_posix(): path.read_bytes()
        for path in sorted(source_pack_root.rglob("*"))
        if path.is_file()
        and (
            "classification" in path.parts
            or "classification" in path.name
            or path.name in {"stage-manifest.json", "artifact-index.json"}
        )
    }


class TestMillefeuilleClassificationBatch(unittest.TestCase):
    def test_batch_classification_routes_sorted_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, first_run = _prepare_fixture_run(tempdir)
            second_run_id = "run-fixture-2"
            second_run = _clone_fixture_run(first_run, second_run_id)
            handoff_path = _write_handoff_jsonl(tempdir)
            _accept_run(
                source_pack_root=root,
                run_id=RUN_ID,
                handoff_path=handoff_path,
            )
            _accept_run(
                source_pack_root=root,
                run_id=second_run_id,
                handoff_path=handoff_path,
            )
            manifest_path = Path(tempdir) / "classification-batch.json"
            first_evidence = Path(tempdir) / "evidence" / "first.json"
            second_evidence = Path(tempdir) / "evidence" / "second.json"
            _write_classification_evidence(
                first_evidence,
                primary_path="Methods > Zeta",
            )
            _write_classification_evidence(
                second_evidence,
                primary_path="Applications > Alpha",
            )
            _write_batch_manifest(
                manifest_path,
                runs=[
                    {
                        "paper_id": PAPER_ID,
                        "run_id": second_run_id,
                        "evidence_ref": "evidence/second.json",
                    },
                    {
                        "paper_id": PAPER_ID,
                        "run_id": RUN_ID,
                        "evidence_ref": "evidence/first.json",
                    },
                ],
            )
            stdout = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=root,
                    manifest_path=manifest_path,
                ),
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "classified")
            self.assertEqual(
                payload["counts"],
                {
                    "runs": 2,
                    "classified": 2,
                    "needs_review": 0,
                    "adjudication_required": 0,
                },
            )
            self.assertEqual(
                [route["primary_path"] for route in payload["routes"]],
                ["Applications > Alpha", "Methods > Zeta"],
            )
            self.assertEqual(
                [run["run_id"] for run in payload["runs"]],
                [RUN_ID, second_run_id],
            )
            self.assertTrue((first_run / CLASSIFICATION_PLAN_REF).is_file())
            self.assertTrue((second_run / CLASSIFICATION_PLAN_REF).is_file())
            batch_dir = root / CLASSIFICATION_BATCH_ROOT_REF / "classification-fixture"
            self.assertTrue((batch_dir / CLASSIFICATION_BATCH_SUMMARY_REF).is_file())
            self.assertTrue((batch_dir / CLASSIFICATION_BATCH_REPORT_REF).is_file())

            first_snapshot = _snapshot_classification_outputs(root)
            rerun_exit = run_stage_cli(
                _batch_args(
                    source_pack_root=root,
                    manifest_path=manifest_path,
                ),
                stdout=StringIO(),
            )
            self.assertEqual(rerun_exit, 0)
            self.assertEqual(_snapshot_classification_outputs(root), first_snapshot)

    def test_batch_classification_aggregates_review_and_adjudication(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, first_run = _prepare_fixture_run(tempdir)
            second_run_id = "run-fixture-2"
            third_run_id = "run-fixture-3"
            second_run = _clone_fixture_run(first_run, second_run_id)
            third_run = _clone_fixture_run(first_run, third_run_id)
            handoff_path = _write_handoff_jsonl(tempdir)
            for run_id in (RUN_ID, second_run_id, third_run_id):
                _accept_run(
                    source_pack_root=root,
                    run_id=run_id,
                    handoff_path=handoff_path,
                )
            manifest_path = Path(tempdir) / "classification-batch.json"
            evidence_dir = Path(tempdir) / "evidence"
            _write_classification_evidence(evidence_dir / "classified.json")
            _write_classification_evidence(
                evidence_dir / "review.json",
                review_reasons=["confidence below batch threshold"],
            )
            _write_classification_evidence(
                evidence_dir / "adjudicate.json",
                review_reasons=["taxonomy gap"],
                adjudication_required=True,
            )
            _write_batch_manifest(
                manifest_path,
                runs=[
                    {
                        "paper_id": PAPER_ID,
                        "run_id": RUN_ID,
                        "evidence_ref": "evidence/classified.json",
                    },
                    {
                        "paper_id": PAPER_ID,
                        "run_id": second_run_id,
                        "evidence_ref": "evidence/review.json",
                    },
                    {
                        "paper_id": PAPER_ID,
                        "run_id": third_run_id,
                        "evidence_ref": "evidence/adjudicate.json",
                    },
                ],
            )
            stdout = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stdout=stdout,
            )

            self.assertEqual(exit_code, 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "adjudication-required")
            self.assertEqual(
                payload["counts"],
                {
                    "runs": 3,
                    "classified": 1,
                    "needs_review": 1,
                    "adjudication_required": 1,
                },
            )
            self.assertTrue((first_run / CLASSIFICATION_PLAN_REF).is_file())
            self.assertTrue((second_run / CLASSIFICATION_PLAN_REF).is_file())
            self.assertTrue((third_run / CLASSIFICATION_PLAN_REF).is_file())

    def test_duplicate_resolved_run_fails_before_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            _accept_run(
                source_pack_root=root,
                run_id=RUN_ID,
                handoff_path=handoff_path,
            )
            evidence_path = Path(tempdir) / "evidence.json"
            _write_classification_evidence(evidence_path)
            manifest_path = Path(tempdir) / "classification-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[
                    {
                        "paper_id": PAPER_ID,
                        "run_id": RUN_ID,
                        "evidence_ref": "evidence.json",
                    },
                    {
                        "item_key": "ITEM1",
                        "run_id": RUN_ID,
                        "evidence_ref": "evidence.json",
                    },
                ],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("unique paper_id/run_id", stderr.getvalue())
            self.assertFalse((run_dir / CLASSIFICATION_PLAN_REF).exists())
            self.assertFalse((root / CLASSIFICATION_BATCH_ROOT_REF).exists())

    def test_later_taxonomy_drift_fails_before_first_run_is_written(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root, first_run = _prepare_fixture_run(tempdir)
            second_run_id = "run-fixture-2"
            _clone_fixture_run(first_run, second_run_id)
            handoff_path = _write_handoff_jsonl(tempdir)
            for run_id in (RUN_ID, second_run_id):
                _accept_run(
                    source_pack_root=root,
                    run_id=run_id,
                    handoff_path=handoff_path,
                )
            evidence_dir = Path(tempdir) / "evidence"
            _write_classification_evidence(evidence_dir / "first.json")
            _write_classification_evidence(
                evidence_dir / "drift.json",
                taxonomy_version="taxonomy-v2",
            )
            manifest_path = Path(tempdir) / "classification-batch.json"
            _write_batch_manifest(
                manifest_path,
                runs=[
                    {
                        "paper_id": PAPER_ID,
                        "run_id": RUN_ID,
                        "evidence_ref": "evidence/first.json",
                    },
                    {
                        "paper_id": PAPER_ID,
                        "run_id": second_run_id,
                        "evidence_ref": "evidence/drift.json",
                    },
                ],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(source_pack_root=root, manifest_path=manifest_path),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("taxonomy drift", stderr.getvalue())
            self.assertFalse((first_run / CLASSIFICATION_PLAN_REF).exists())
            self.assertFalse((root / CLASSIFICATION_BATCH_ROOT_REF).exists())

    def test_unsafe_evidence_ref_and_non_string_values_are_rejected(self):
        invalid_runs = (
            (
                [{"paper_id": PAPER_ID, "run_id": RUN_ID, "evidence_ref": "../x"}],
                "traversal-safe relative path",
            ),
            (
                [{"paper_id": PAPER_ID, "run_id": RUN_ID, "evidence_ref": None}],
                "non-string fields",
            ),
        )
        for runs, expected_error in invalid_runs:
            with (
                self.subTest(expected_error=expected_error),
                tempfile.TemporaryDirectory() as tempdir,
            ):
                root, run_dir = _prepare_fixture_run(tempdir)
                handoff_path = _write_handoff_jsonl(tempdir)
                _accept_run(
                    source_pack_root=root,
                    run_id=RUN_ID,
                    handoff_path=handoff_path,
                )
                manifest_path = Path(tempdir) / "classification-batch.json"
                _write_batch_manifest(manifest_path, runs=runs)
                stderr = StringIO()

                exit_code = run_stage_cli(
                    _batch_args(
                        source_pack_root=root,
                        manifest_path=manifest_path,
                    ),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertIn(expected_error, stderr.getvalue())
                self.assertFalse((run_dir / CLASSIFICATION_PLAN_REF).exists())
                self.assertFalse((root / CLASSIFICATION_BATCH_ROOT_REF).exists())


if __name__ == "__main__":
    unittest.main()
