"""Tests for offline Millefeuille stage commands."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain.acceptance import ACCEPTANCE_SUMMARY_REF
from millefeuille.domain.artifact_writer import write_dry_run_artifacts
from millefeuille.domain.artifacts import load_artifact_index
from millefeuille.domain.card_fixtures import write_cards_from_evidence
from millefeuille.domain.classification import (
    CLASSIFICATION_PLAN_REF,
    WRITEBACK_PREVIEW_REF,
)
from millefeuille.domain.config import ArtifactExportConfig
from millefeuille.domain.extraction_fixtures import (
    write_native_extractions_from_evidence,
    write_ocr_extractions_from_evidence,
)
from millefeuille.domain.index_fixtures import write_indexes_from_evidence
from millefeuille.domain.release_preflight import RELEASE_PREFLIGHT_JSON_REF
from millefeuille.domain.route_fixtures import write_route_selections_from_evidence
from millefeuille.domain.source_packs import write_source_pack_from_recovered_pdf
from millefeuille.domain.stage_runtime import load_stage_manifest
from millefeuille.domain.structure_fixtures import write_structures_from_evidence
from millefeuille.domain.summary_fixtures import write_summaries_from_evidence
from millefeuille.domain.writeback import WRITEBACK_PLAN_REF
from tests.test_millefeuille_source_pack_writer import (
    _evidence,
    _make_handoff_row,
    _make_item,
    _write_card_evidence_json,
    _write_card_fixture_json,
    _write_index_evidence_json,
    _write_index_fixture_json,
    _write_markdown,
    _write_native_extraction_evidence_json,
    _write_ocr_extraction_evidence_json,
    _write_recovered_pdf,
    _write_route_selection_evidence_json,
    _write_structure_evidence_json,
    _write_structure_payload_json,
    _write_summary_evidence_json,
    _write_summary_fixture_json,
)

RUN_ID = "run-fixture"
PAPER_ID = "zotero-ITEM1"


def _prepare_fixture_run(tempdir: str) -> tuple[Path, Path]:
    source_pack_root = Path(tempdir) / "source-packs"
    source_path = _write_recovered_pdf(tempdir)
    write_source_pack_from_recovered_pdf(
        evidence=_evidence(source_path),
        source_pack_root=source_pack_root,
        created_at="2026-07-14T01:00:00+00:00",
    )

    native_markdown = _write_markdown(tempdir, "native-fulltext.md", "Native text\n")
    native_evidence = _write_native_extraction_evidence_json(
        tempdir,
        native_markdown,
    )
    write_native_extractions_from_evidence(
        evidence_path=native_evidence,
        source_pack_root=source_pack_root,
    )

    ocr_markdown = _write_markdown(tempdir, "ocr-fulltext.md", "OCR text\n")
    ocr_evidence = _write_ocr_extraction_evidence_json(tempdir, ocr_markdown)
    write_ocr_extractions_from_evidence(
        evidence_path=ocr_evidence,
        source_pack_root=source_pack_root,
    )

    route_markdown = _write_markdown(tempdir, "selected-fulltext.md", "Selected text\n")
    route_evidence = _write_route_selection_evidence_json(tempdir, route_markdown)
    write_route_selections_from_evidence(
        evidence_path=route_evidence,
        source_pack_root=source_pack_root,
    )

    structure_payload = _write_structure_payload_json(tempdir)
    outline_path = _write_markdown(tempdir, "outline.md", "# Outline\n")
    structure_evidence = _write_structure_evidence_json(
        tempdir,
        structure_payload,
        outline_path,
    )
    write_structures_from_evidence(
        evidence_path=structure_evidence,
        source_pack_root=source_pack_root,
    )

    _write_markdown(tempdir, "page-1.md", "Page 1 summary.\n")
    _write_markdown(tempdir, "full-paper.md", "Full paper summary.\n")
    summary_fixture = _write_summary_fixture_json(tempdir)
    summary_evidence = _write_summary_evidence_json(tempdir, summary_fixture)
    write_summaries_from_evidence(
        evidence_path=summary_evidence,
        source_pack_root=source_pack_root,
        run_id=RUN_ID,
    )

    card_fixture = _write_card_fixture_json(tempdir)
    card_markdown = _write_markdown(tempdir, "paper-card.md", "# Card\n")
    card_evidence = _write_card_evidence_json(tempdir, card_fixture, card_markdown)
    write_cards_from_evidence(
        evidence_path=card_evidence,
        source_pack_root=source_pack_root,
        run_id=RUN_ID,
    )

    index_fixture = _write_index_fixture_json(tempdir)
    index_evidence = _write_index_evidence_json(tempdir, index_fixture)
    write_indexes_from_evidence(
        evidence_path=index_evidence,
        source_pack_root=source_pack_root,
        run_id=RUN_ID,
    )

    write_dry_run_artifacts(
        items=[_make_item()],
        handoff_rows=[_make_handoff_row()],
        config=ArtifactExportConfig(
            enabled=True,
            artifact_root="source-pack",
            source_pack_root=str(source_pack_root),
            run_id=RUN_ID,
        ),
        handoff_enabled=True,
    )

    run_dir = (
        source_pack_root / "zotero" / PAPER_ID / "analyses" / "millefeuille" / RUN_ID
    )
    return source_pack_root, run_dir


def _write_handoff_jsonl(tempdir: str) -> Path:
    path = Path(tempdir) / "handoff.jsonl"
    path.write_text(
        json.dumps(_make_handoff_row().to_dict()) + "\n",
        encoding="utf-8",
    )
    return path


def _write_classification_evidence_json(tempdir: str) -> Path:
    path = Path(tempdir) / "classification-evidence.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": ("millefeuille-classification-fixture-evidence/v0.1"),
                "taxonomy_version": "taxonomy-v1",
                "mode": "single",
                "primary_path": "Methods > Normalization & training dynamics",
                "confidence": "high",
                "evidence_refs": [
                    "summaries/hierarchical-summary.json",
                    "cards/paper-card.json",
                ],
                "rejected_alternatives": [
                    {
                        "path": "Applications > Vision",
                        "reason": "the primary contribution is methodological",
                    }
                ],
                "writeback_preview": {
                    "add_tags": ["millefeuille-classified"],
                    "remove_tags": ["millefeuille"],
                    "destination_collection": (
                        "Methods · Normalization & training dynamics"
                    ),
                    "note_markdown": "Classification preview note.",
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class TestMillefeuilleStageCli(unittest.TestCase):
    def test_acceptance_command_writes_summary_and_updates_status(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            stdout = StringIO()

            exit_code = run_stage_cli(
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
                    "--json",
                ],
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "pass")
            summary = json.loads(
                (run_dir / ACCEPTANCE_SUMMARY_REF).read_text(encoding="utf-8")
            )
            self.assertEqual(summary["status"], "pass")

            stage_manifest = load_stage_manifest(run_dir / "stage-manifest.json")
            stage_map = {
                stage.name.value: stage.status.value for stage in stage_manifest.stages
            }
            self.assertEqual(stage_map["acceptance"], "passed")

            artifact_index = load_artifact_index(run_dir / "artifact-index.json")
            self.assertIn("acceptance_summary", artifact_index.artifacts)

    def test_classify_and_writeback_commands_materialize_preview_artifacts(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            classification_path = _write_classification_evidence_json(tempdir)
            run_stage_cli(
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
                ]
            )

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
                ]
            )
            writeback_exit = run_stage_cli(
                [
                    "writeback",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                ]
            )

            self.assertEqual(classify_exit, 0)
            self.assertEqual(writeback_exit, 0)
            self.assertTrue((run_dir / CLASSIFICATION_PLAN_REF).is_file())
            self.assertTrue((run_dir / WRITEBACK_PREVIEW_REF).is_file())
            self.assertTrue((run_dir / WRITEBACK_PLAN_REF).is_file())

            stage_manifest = load_stage_manifest(run_dir / "stage-manifest.json")
            stage_map = {
                stage.name.value: stage.status.value for stage in stage_manifest.stages
            }
            self.assertEqual(stage_map["classify"], "passed")
            self.assertEqual(stage_map["writeback"], "passed")

            artifact_index = load_artifact_index(run_dir / "artifact-index.json")
            self.assertEqual(artifact_index.zotero_writeback["mode"], "preview")
            self.assertEqual(
                artifact_index.zotero_writeback["status"],
                "previewed",
            )

    def test_run_retrieve_and_models_commands_cover_full_preview_chain(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            classification_path = _write_classification_evidence_json(tempdir)
            stdout = StringIO()

            exit_code = run_stage_cli(
                [
                    "run",
                    "--source-pack-root",
                    str(source_pack_root),
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
                    "--candidate-version",
                    "0.5.0-rc1",
                    "--json",
                ],
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertIn("release_preflight", payload)
            self.assertTrue((run_dir / RELEASE_PREFLIGHT_JSON_REF).is_file())

            retrieve_stdout = StringIO()
            retrieve_exit = run_stage_cli(
                [
                    "retrieve",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                    "--summary-scope",
                    "classification",
                    "--json",
                ],
                stdout=retrieve_stdout,
            )
            self.assertEqual(retrieve_exit, 0)
            retrieved = json.loads(retrieve_stdout.getvalue())
            self.assertEqual(retrieved["paper_id"], PAPER_ID)
            self.assertIn("classification_plan_ref", retrieved)
            self.assertEqual(len(retrieved["summary_entries"]), 1)

            models_stdout = StringIO()
            models_exit = run_stage_cli(["models", "--json"], stdout=models_stdout)
            self.assertEqual(models_exit, 0)
            models_payload = json.loads(models_stdout.getvalue())
            self.assertEqual(models_payload["default_profile"], "research-default")


if __name__ == "__main__":
    unittest.main()
