"""Tests for deterministic offline batch acceptance."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.acceptance import (
    ACCEPTANCE_BATCH_ROOT_REF,
    ACCEPTANCE_BATCH_SUMMARY_MARKDOWN_REF,
    ACCEPTANCE_BATCH_SUMMARY_REF,
    ACCEPTANCE_SUMMARY_REF,
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
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _clone_fixture_run(run_dir: Path, run_id: str) -> Path:
    destination = run_dir.parent / run_id
    shutil.copytree(run_dir, destination)
    for relative_path in (
        Path("stage-manifest.json"),
        Path("artifact-index.json"),
        SUMMARY_ARTIFACT_REF,
        INDEX_STATUS_REF,
    ):
        target = destination / relative_path
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["run_id"] = run_id
        _write_json(target, payload)
    return destination


def _write_batch_manifest(
    tempdir: str,
    *,
    batch_id: object = "batch-fixture",
    runs: list[dict[str, object]],
) -> Path:
    path = Path(tempdir) / "acceptance-batch.json"
    _write_json(
        path,
        {
            "schema_version": "millefeuille-acceptance-batch-manifest/v0.1",
            "batch_id": batch_id,
            "runs": runs,
        },
    )
    return path


def _batch_args(
    *,
    source_pack_root: Path,
    manifest_path: Path,
    handoff_path: Path,
) -> list[str]:
    return [
        "acceptance",
        "--source-pack-root",
        str(source_pack_root),
        "--batch-manifest",
        str(manifest_path),
        "--handoff",
        str(handoff_path),
        "--json",
    ]


def _snapshot_acceptance_outputs(source_pack_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(source_pack_root).as_posix(): path.read_bytes()
        for path in sorted(source_pack_root.rglob("*"))
        if path.is_file()
        and (
            "acceptance-summary" in path.name
            or "acceptance-batch-summary" in path.name
        )
    }


class TestMillefeuilleAcceptanceBatch(unittest.TestCase):
    def test_batch_acceptance_writes_sorted_idempotent_aggregate(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            second_run_id = "run-fixture-2"
            second_run_dir = _clone_fixture_run(run_dir, second_run_id)
            handoff_path = _write_handoff_jsonl(tempdir)
            manifest_path = _write_batch_manifest(
                tempdir,
                runs=[
                    {"paper_id": PAPER_ID, "run_id": second_run_id},
                    {"paper_id": PAPER_ID, "run_id": RUN_ID},
                ],
            )
            stdout = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=source_pack_root,
                    manifest_path=manifest_path,
                    handoff_path=handoff_path,
                ),
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(
                payload["counts"],
                {"needs_review": 0, "passed": 2, "runs": 2},
            )
            self.assertEqual(
                [run["run_id"] for run in payload["runs"]],
                [RUN_ID, second_run_id],
            )
            self.assertTrue((run_dir / ACCEPTANCE_SUMMARY_REF).is_file())
            self.assertTrue((second_run_dir / ACCEPTANCE_SUMMARY_REF).is_file())
            batch_dir = source_pack_root / ACCEPTANCE_BATCH_ROOT_REF / "batch-fixture"
            self.assertTrue((batch_dir / ACCEPTANCE_BATCH_SUMMARY_REF).is_file())
            self.assertTrue(
                (batch_dir / ACCEPTANCE_BATCH_SUMMARY_MARKDOWN_REF).is_file()
            )

            first_snapshot = _snapshot_acceptance_outputs(source_pack_root)
            rerun_exit = run_stage_cli(
                _batch_args(
                    source_pack_root=source_pack_root,
                    manifest_path=manifest_path,
                    handoff_path=handoff_path,
                )
            )
            self.assertEqual(rerun_exit, 0)
            self.assertEqual(
                _snapshot_acceptance_outputs(source_pack_root),
                first_snapshot,
            )

    def test_batch_acceptance_aggregates_needs_review_without_skipping_runs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            second_run_id = "run-fixture-2"
            second_run_dir = _clone_fixture_run(run_dir, second_run_id)
            index_path = second_run_dir / INDEX_STATUS_REF
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
            index_payload["duplicate_scan"] = {
                "matched_existing": True,
                "match_count": 1,
            }
            _write_json(index_path, index_payload)
            handoff_path = _write_handoff_jsonl(tempdir)
            manifest_path = _write_batch_manifest(
                tempdir,
                runs=[
                    {"paper_id": PAPER_ID, "run_id": RUN_ID},
                    {"paper_id": PAPER_ID, "run_id": second_run_id},
                ],
            )
            stdout = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=source_pack_root,
                    manifest_path=manifest_path,
                    handoff_path=handoff_path,
                ),
                stdout=stdout,
            )

            self.assertEqual(exit_code, 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "needs-review")
            self.assertEqual(
                payload["counts"],
                {"needs_review": 1, "passed": 1, "runs": 2},
            )
            self.assertTrue((run_dir / ACCEPTANCE_SUMMARY_REF).is_file())
            review_summary = json.loads(
                (second_run_dir / ACCEPTANCE_SUMMARY_REF).read_text(encoding="utf-8")
            )
            self.assertEqual(review_summary["status"], "needs-review")

    def test_duplicate_batch_run_fails_before_acceptance_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            manifest_path = _write_batch_manifest(
                tempdir,
                runs=[
                    {"paper_id": PAPER_ID, "run_id": RUN_ID},
                    {"item_key": "ITEM1", "run_id": RUN_ID},
                ],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=source_pack_root,
                    manifest_path=manifest_path,
                    handoff_path=handoff_path,
                ),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("unique paper_id/run_id", stderr.getvalue())
            self.assertFalse((run_dir / ACCEPTANCE_SUMMARY_REF).exists())
            self.assertFalse((source_pack_root / ACCEPTANCE_BATCH_ROOT_REF).exists())

    def test_missing_later_run_fails_before_first_run_is_written(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            manifest_path = _write_batch_manifest(
                tempdir,
                runs=[
                    {"paper_id": PAPER_ID, "run_id": RUN_ID},
                    {"paper_id": PAPER_ID, "run_id": "missing-run"},
                ],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=source_pack_root,
                    manifest_path=manifest_path,
                    handoff_path=handoff_path,
                ),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("stage manifest not found", stderr.getvalue())
            self.assertFalse((run_dir / ACCEPTANCE_SUMMARY_REF).exists())
            self.assertFalse((source_pack_root / ACCEPTANCE_BATCH_ROOT_REF).exists())

    def test_traversal_batch_id_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            manifest_path = _write_batch_manifest(
                tempdir,
                batch_id="../escape",
                runs=[{"paper_id": PAPER_ID, "run_id": RUN_ID}],
            )
            stderr = StringIO()

            exit_code = run_stage_cli(
                _batch_args(
                    source_pack_root=source_pack_root,
                    manifest_path=manifest_path,
                    handoff_path=handoff_path,
                ),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("batch_id must be traversal-safe", stderr.getvalue())
            self.assertFalse((run_dir / ACCEPTANCE_SUMMARY_REF).exists())
            self.assertFalse((source_pack_root / ACCEPTANCE_BATCH_ROOT_REF).exists())

    def test_non_string_manifest_values_are_not_coerced(self):
        invalid_manifests = (
            (123, [{"paper_id": PAPER_ID, "run_id": RUN_ID}], "batch_id"),
            (
                "batch-fixture",
                [{"paper_id": PAPER_ID, "run_id": None}],
                "non-string locator",
            ),
        )
        for batch_id, runs, expected_error in invalid_manifests:
            with (
                self.subTest(expected_error=expected_error),
                tempfile.TemporaryDirectory() as tempdir,
            ):
                source_pack_root, run_dir = _prepare_fixture_run(tempdir)
                handoff_path = _write_handoff_jsonl(tempdir)
                manifest_path = _write_batch_manifest(
                    tempdir,
                    batch_id=batch_id,
                    runs=runs,
                )
                stderr = StringIO()

                exit_code = run_stage_cli(
                    _batch_args(
                        source_pack_root=source_pack_root,
                        manifest_path=manifest_path,
                        handoff_path=handoff_path,
                    ),
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertIn(expected_error, stderr.getvalue())
                self.assertFalse((run_dir / ACCEPTANCE_SUMMARY_REF).exists())
                self.assertFalse(
                    (source_pack_root / ACCEPTANCE_BATCH_ROOT_REF).exists()
                )


if __name__ == "__main__":
    unittest.main()
