"""Focused tests for explicit lifecycle artifact-package locators."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import shutil
import stat
import tempfile
from types import SimpleNamespace
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.acceptance import ACCEPTANCE_SUMMARY_REF
from millefeuille.domain.artifact_writer import write_dry_run_artifacts
from millefeuille.domain.classification import (
    CLASSIFICATION_PLAN_REF,
    WRITEBACK_PREVIEW_REF,
)
from millefeuille.domain.config import ArtifactExportConfig
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.release_preflight import RELEASE_PREFLIGHT_JSON_REF
from millefeuille.domain.stage_runtime import (
    _is_path_indirection,
    load_stage_manifest,
    resolve_run_artifacts,
)
from millefeuille.domain.summary_fixtures import SUMMARY_ARTIFACT_REF
from millefeuille.domain.writeback import WRITEBACK_PLAN_REF
from tests.test_millefeuille_source_pack_writer import (
    _make_handoff_row,
    _make_item,
)
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_source_pack_run,
    _write_classification_evidence_json,
    _write_handoff_jsonl,
    _write_offline_stage_evidence,
)


def _prepare_external_run(tempdir: str) -> tuple[Path, Path, Path]:
    source_pack_root, canonical_run_dir = _prepare_source_pack_run(tempdir)
    artifact_root = Path(tempdir) / "artifacts"
    write_dry_run_artifacts(
        items=[_make_item()],
        handoff_rows=[_make_handoff_row()],
        config=ArtifactExportConfig(
            enabled=True,
            artifact_root=str(artifact_root),
            source_pack_root=str(source_pack_root),
            run_id=RUN_ID,
        ),
        handoff_enabled=True,
    )
    return source_pack_root, artifact_root, canonical_run_dir


def _run_locator_args(source_pack_root: Path, artifact_root: Path) -> list[str]:
    return [
        "--source-pack-root",
        str(source_pack_root),
        "--artifact-root",
        str(artifact_root),
        "--paper-id",
        PAPER_ID,
        "--run-id",
        RUN_ID,
    ]


class TestMillefeuilleArtifactOverrides(unittest.TestCase):
    def test_external_root_and_custom_manifest_route_run_scoped_writes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, artifact_root, canonical_run_dir = (
                _prepare_external_run(tempdir)
            )
            evidence = _write_offline_stage_evidence(tempdir)
            setup_exit = run_stage_cli(
                [
                    "run",
                    *_run_locator_args(source_pack_root, artifact_root),
                    "--stages",
                    "extract-native,extract-ocr,route,structure",
                    "--native-extraction-evidence",
                    str(evidence["native"]),
                    "--ocr-extraction-evidence",
                    str(evidence["ocr"]),
                    "--route-selection-evidence",
                    str(evidence["route"]),
                    "--structure-evidence",
                    str(evidence["structure"]),
                ],
                stdout=StringIO(),
            )
            self.assertEqual(setup_exit, 0)

            run_dir = artifact_root / PAPER_ID / RUN_ID
            default_manifest = run_dir / "stage-manifest.json"
            custom_manifest = run_dir / "run-state.json"
            default_manifest.replace(custom_manifest)
            index_path = run_dir / "artifact-index.json"
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
            for stage_record in index_payload["stages"].values():
                stage_record["manifest_ref"] = custom_manifest.name
            index_payload["artifacts"]["stage_manifest"]["ref"] = (
                custom_manifest.name
            )
            index_path.write_text(
                json.dumps(index_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            stderr = StringIO()
            summarize_exit = run_stage_cli(
                [
                    "summarize",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--stage-manifest",
                    str(custom_manifest),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                    "--evidence",
                    str(evidence["summary"]),
                ],
                stdout=StringIO(),
                stderr=stderr,
            )

            self.assertEqual(summarize_exit, 0, stderr.getvalue())
            self.assertTrue((run_dir / SUMMARY_ARTIFACT_REF).is_file())
            self.assertFalse((canonical_run_dir / SUMMARY_ARTIFACT_REF).exists())
            self.assertFalse(default_manifest.exists())
            statuses = {
                record.name.value: record.status.value
                for record in load_stage_manifest(custom_manifest).stages
            }
            self.assertEqual(statuses["summarize"], "passed")
            persisted_index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertTrue(
                all(
                    record["manifest_ref"] == custom_manifest.name
                    for record in persisted_index["stages"].values()
                )
            )

            for command, evidence_name in (("card", "card"), ("index", "index")):
                stderr = StringIO()
                exit_code = run_stage_cli(
                    [
                        command,
                        "--source-pack-root",
                        str(source_pack_root),
                        "--stage-manifest",
                        str(custom_manifest),
                        "--paper-id",
                        PAPER_ID,
                        "--run-id",
                        RUN_ID,
                        "--evidence",
                        str(evidence[evidence_name]),
                    ],
                    stdout=StringIO(),
                    stderr=stderr,
                )
                self.assertEqual(exit_code, 0, stderr.getvalue())

            handoff_path = _write_handoff_jsonl(tempdir)
            classification_path = _write_classification_evidence_json(tempdir)
            lifecycle_stderr = StringIO()
            lifecycle_exit = run_stage_cli(
                [
                    "run",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--stage-manifest",
                    str(custom_manifest),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                    "--stages",
                    "acceptance,classify,writeback",
                    "--handoff",
                    str(handoff_path),
                    "--classification-evidence",
                    str(classification_path),
                    "--release-preflight",
                ],
                stdout=StringIO(),
                stderr=lifecycle_stderr,
            )
            self.assertEqual(lifecycle_exit, 0, lifecycle_stderr.getvalue())
            for ref in (
                ACCEPTANCE_SUMMARY_REF,
                CLASSIFICATION_PLAN_REF,
                WRITEBACK_PREVIEW_REF,
                WRITEBACK_PLAN_REF,
                RELEASE_PREFLIGHT_JSON_REF,
            ):
                self.assertTrue((run_dir / ref).is_file(), ref)
                self.assertFalse((canonical_run_dir / ref).exists(), ref)

            retrieve_stdout = StringIO()
            retrieve_stderr = StringIO()
            retrieve_exit = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--stage-manifest",
                    str(custom_manifest),
                    "--title",
                    "Fixture Paper",
                    "--run-id",
                    RUN_ID,
                    "--json",
                ],
                stdout=retrieve_stdout,
                stderr=retrieve_stderr,
            )
            self.assertEqual(retrieve_exit, 0, retrieve_stderr.getvalue())
            self.assertEqual(
                json.loads(retrieve_stdout.getvalue())["paper_id"],
                PAPER_ID,
            )

    def test_root_layout_resolution_is_exact_and_unambiguous(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, artifact_root, canonical_run_dir = (
                _prepare_external_run(tempdir)
            )
            run_dir = artifact_root / PAPER_ID / RUN_ID

            from_container = resolve_run_artifacts(
                source_pack_root=source_pack_root,
                artifact_root=artifact_root,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            from_run_dir = resolve_run_artifacts(
                source_pack_root=source_pack_root,
                artifact_root=run_dir,
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            self.assertEqual(from_container.run_dir, run_dir)
            self.assertEqual(from_run_dir.run_dir, run_dir)
            explicit_source_pack = resolve_run_artifacts(
                source_pack_root=source_pack_root,
                artifact_root=Path("source-pack"),
                paper_id=PAPER_ID,
                run_id=RUN_ID,
            )
            self.assertEqual(explicit_source_pack.run_dir, canonical_run_dir)

            empty_root = Path(tempdir) / "empty-artifacts"
            empty_root.mkdir()
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "contains no complete run package",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=empty_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            shutil.copytree(run_dir, artifact_root / RUN_ID)
            with self.assertRaisesRegex(MillefeuilleContractError, "ambiguous"):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=artifact_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

    def test_traversal_cross_wiring_and_identity_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, artifact_root, _canonical_run_dir = (
                _prepare_external_run(tempdir)
            )
            run_dir = artifact_root / PAPER_ID / RUN_ID

            traversal_root = artifact_root / ".." / artifact_root.name
            with self.assertRaisesRegex(MillefeuilleContractError, "parent traversal"):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=traversal_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "surrounding whitespace",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=f" {artifact_root}",
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            traversal_manifest = run_dir / ".." / RUN_ID / "stage-manifest.json"
            with self.assertRaisesRegex(MillefeuilleContractError, "parent traversal"):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    stage_manifest=traversal_manifest,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            other_root = Path(tempdir) / "other-artifacts"
            other_run = other_root / PAPER_ID / RUN_ID
            shutil.copytree(run_dir, other_run)
            other_index = json.loads(
                (other_run / "artifact-index.json").read_text(encoding="utf-8")
            )
            other_index["artifact_root"] = str(other_run)
            (other_run / "artifact-index.json").write_text(
                json.dumps(other_index, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "does not belong to the selected artifact root",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=artifact_root,
                    stage_manifest=other_run / "stage-manifest.json",
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            index_path = run_dir / "artifact-index.json"
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
            index_payload["artifact_root"] = str(other_run)
            index_path.write_text(
                json.dumps(index_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "artifact_root drift",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=artifact_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            index_payload["artifact_root"] = str(run_dir)
            first_stage = next(iter(index_payload["stages"].values()))
            first_stage["manifest_ref"] = "other-manifest.json"
            index_path.write_text(
                json.dumps(index_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "stage manifest ref drift",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=artifact_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            first_stage["manifest_ref"] = "stage-manifest.json"
            index_payload["artifacts"]["stage_manifest"]["ref"] = (
                "other-manifest.json"
            )
            index_path.write_text(
                json.dumps(index_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "stage manifest artifact ref drift",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=artifact_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            index_payload["artifacts"]["stage_manifest"]["ref"] = (
                "stage-manifest.json"
            )
            index_payload["source_pack"]["ref"] = str(Path(tempdir) / "wrong-pack")
            index_path.write_text(
                json.dumps(index_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "source-pack ref drift",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=artifact_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

            index_payload["source_pack"]["ref"] = str(
                source_pack_root / "zotero" / PAPER_ID
            )
            index_path.write_text(
                json.dumps(index_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest_path = run_dir / "stage-manifest.json"
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_payload["run_id"] = "other-run"
            manifest_path.write_text(
                json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "stage manifest run_id drift",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=artifact_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

    def test_symlink_and_reparse_indirection_are_rejected(self):
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        self.assertTrue(
            _is_path_indirection(
                SimpleNamespace(
                    st_mode=stat.S_IFDIR,
                    st_file_attributes=reparse_flag,
                )
            )
        )

        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, artifact_root, _canonical_run_dir = (
                _prepare_external_run(tempdir)
            )
            linked_root = Path(tempdir) / "linked-artifacts"
            try:
                linked_root.symlink_to(artifact_root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(
                MillefeuilleContractError,
                "symbolic link or reparse point",
            ):
                resolve_run_artifacts(
                    source_pack_root=source_pack_root,
                    artifact_root=linked_root,
                    paper_id=PAPER_ID,
                    run_id=RUN_ID,
                )

    def test_batch_commands_reject_single_run_overrides(self):
        command_args = (
            [
                "acceptance",
                "--source-pack-root",
                "unused",
                "--batch-manifest",
                "batch.json",
                "--handoff",
                "handoff.jsonl",
            ],
            [
                "classify",
                "--source-pack-root",
                "unused",
                "--batch-manifest",
                "batch.json",
            ],
            [
                "retrieve",
                "--source-pack-root",
                "unused",
                "--batch-manifest",
                "batch.json",
            ],
        )
        for args in command_args:
            with self.subTest(command=args[0]):
                stderr = StringIO()
                exit_code = run_stage_cli(
                    [*args, "--artifact-root", "artifacts"],
                    stderr=stderr,
                )
                self.assertEqual(exit_code, 2)
                self.assertIn(
                    "cannot be combined with --artifact-root or --stage-manifest",
                    stderr.getvalue(),
                )


if __name__ == "__main__":
    unittest.main()
