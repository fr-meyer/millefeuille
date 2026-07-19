"""Tests for offline Millefeuille stage commands."""

from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

import yaml

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


def _prepare_source_pack_run(tempdir: str) -> tuple[Path, Path]:
    source_pack_root = Path(tempdir) / "source-packs"
    source_path = _write_recovered_pdf(tempdir)
    write_source_pack_from_recovered_pdf(
        evidence=_evidence(source_path),
        source_pack_root=source_pack_root,
        created_at="2026-07-14T01:00:00+00:00",
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


def _write_offline_stage_evidence(tempdir: str) -> dict[str, Path]:
    native_markdown = _write_markdown(tempdir, "native-fulltext.md", "Native text\n")
    native_evidence = _write_native_extraction_evidence_json(
        tempdir,
        native_markdown,
    )
    ocr_markdown = _write_markdown(tempdir, "ocr-fulltext.md", "OCR text\n")
    ocr_evidence = _write_ocr_extraction_evidence_json(tempdir, ocr_markdown)
    route_markdown = _write_markdown(tempdir, "selected-fulltext.md", "Selected\n")
    route_evidence = _write_route_selection_evidence_json(tempdir, route_markdown)
    structure_payload = _write_structure_payload_json(tempdir)
    outline_path = _write_markdown(tempdir, "outline.md", "# Outline\n")
    structure_evidence = _write_structure_evidence_json(
        tempdir,
        structure_payload,
        outline_path,
    )
    _write_markdown(tempdir, "page-1.md", "Page 1 summary.\n")
    _write_markdown(tempdir, "full-paper.md", "Full paper summary.\n")
    summary_fixture = _write_summary_fixture_json(tempdir)
    summary_evidence = _write_summary_evidence_json(tempdir, summary_fixture)
    card_fixture = _write_card_fixture_json(tempdir)
    card_markdown = _write_markdown(tempdir, "paper-card.md", "# Card\n")
    card_evidence = _write_card_evidence_json(
        tempdir,
        card_fixture,
        card_markdown,
    )
    index_fixture = _write_index_fixture_json(tempdir)
    index_evidence = _write_index_evidence_json(tempdir, index_fixture)
    return {
        "native": native_evidence,
        "ocr": ocr_evidence,
        "route": route_evidence,
        "structure": structure_evidence,
        "summary": summary_evidence,
        "card": card_evidence,
        "index": index_evidence,
    }


def _full_run_args(
    *,
    source_pack_root: Path,
    evidence: dict[str, Path],
    handoff_path: Path,
    classification_path: Path,
) -> list[str]:
    return [
        "run",
        "--source-pack-root",
        str(source_pack_root),
        "--paper-id",
        PAPER_ID,
        "--run-id",
        RUN_ID,
        "--stages",
        (
            "extract-native,extract-ocr,route,structure,summarize,card,index,"
            "acceptance,classify,writeback"
        ),
        "--native-extraction-evidence",
        str(evidence["native"]),
        "--ocr-extraction-evidence",
        str(evidence["ocr"]),
        "--route-selection-evidence",
        str(evidence["route"]),
        "--structure-evidence",
        str(evidence["structure"]),
        "--summary-evidence",
        str(evidence["summary"]),
        "--card-evidence",
        str(evidence["card"]),
        "--index-evidence",
        str(evidence["index"]),
        "--handoff",
        str(handoff_path),
        "--classification-evidence",
        str(classification_path),
        "--json",
    ]


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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
            research_profile = models_payload["profiles"]["research-default"]
            for stage_name in (
                "summarize_page",
                "summarize_section",
                "summarize_full_paper",
            ):
                self.assertEqual(
                    research_profile[stage_name]["model"],
                    "openai/gpt-5.6-sol",
                )
                self.assertEqual(
                    research_profile[stage_name]["reasoning_effort"],
                    "xhigh",
                )
                self.assertEqual(research_profile[stage_name]["fast_mode"], "off")
                self.assertEqual(
                    research_profile[stage_name]["auth_lane"],
                    "openclaw-native-codex-oauth",
                )
                self.assertEqual(
                    research_profile[stage_name]["fallback_policy"],
                    "none",
                )

            self.assertEqual(
                research_profile["paper_card"],
                {
                    "backend": "chat",
                    "model": "gpt-5",
                    "provider": "openai",
                    "temperature": 0.1,
                    "prompt_version": "paper-card-v1",
                    "record_usage": True,
                },
            )
            self.assertEqual(
                research_profile["classify"],
                {
                    "backend": "chat",
                    "model": "gpt-5",
                    "provider": "openai",
                    "temperature": 0.0,
                    "prompt_version": "classify-v1",
                    "require_taxonomy_version": True,
                    "record_usage": True,
                },
            )
            self.assertEqual(
                models_payload["profiles"]["offline-preview"],
                {
                    "summarize_page": {
                        "auth_lane": "none",
                        "backend": "fixture",
                        "fallback_policy": "none",
                        "model": "offline-preview",
                        "provider": "none",
                        "record_usage": False,
                    },
                    "summarize_section": {
                        "auth_lane": "none",
                        "backend": "fixture",
                        "fallback_policy": "none",
                        "model": "offline-preview",
                        "provider": "none",
                        "record_usage": False,
                    },
                    "summarize_full_paper": {
                        "auth_lane": "none",
                        "backend": "fixture",
                        "fallback_policy": "none",
                        "model": "offline-preview",
                        "provider": "none",
                        "record_usage": False,
                    },
                    "paper_card": {
                        "backend": "fixture",
                        "model": "offline-preview",
                        "provider": "none",
                        "record_usage": False,
                    },
                    "classify": {
                        "backend": "fixture",
                        "model": "offline-preview",
                        "provider": "none",
                        "require_taxonomy_version": True,
                        "record_usage": False,
                    },
                },
            )

    def test_model_profile_schema_enumerates_reasoning_and_fast_modes(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "specs"
            / "millefeuille-pipeline"
            / "model-profile.schema.yaml"
        )
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        stage_properties = schema["$defs"]["model_stage"]["properties"]

        self.assertEqual(
            stage_properties["reasoning_effort"]["enum"],
            ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
        )
        self.assertEqual(
            stage_properties["fast_mode"]["enum"],
            ["off", "on", "auto"],
        )

    def test_run_executes_full_fixture_pipeline_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_source_pack_run(tempdir)
            evidence = _write_offline_stage_evidence(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            classification_path = _write_classification_evidence_json(tempdir)
            args = _full_run_args(
                source_pack_root=source_pack_root,
                evidence=evidence,
                handoff_path=handoff_path,
                classification_path=classification_path,
            )

            first_stdout = StringIO()
            first_exit = run_stage_cli(args, stdout=first_stdout)

            self.assertEqual(first_exit, 0)
            first_payload = json.loads(first_stdout.getvalue())
            self.assertEqual(first_payload["resumed"], [])
            self.assertEqual(first_payload["extract-native"]["write_status"], "created")
            stage_manifest = load_stage_manifest(run_dir / "stage-manifest.json")
            stage_map = {
                stage.name.value: stage.status.value for stage in stage_manifest.stages
            }
            for stage_name in (
                "extract-native",
                "extract-ocr",
                "route",
                "structure",
                "summarize",
                "card",
                "index",
                "acceptance",
                "classify",
                "writeback",
            ):
                self.assertEqual(stage_map[stage_name], "passed")

            first_snapshot = _snapshot_files(source_pack_root)
            second_stdout = StringIO()
            second_exit = run_stage_cli(args, stdout=second_stdout)

            self.assertEqual(second_exit, 0)
            second_payload = json.loads(second_stdout.getvalue())
            self.assertEqual(
                second_payload["extract-native"]["write_status"],
                "existing",
            )
            self.assertEqual(_snapshot_files(source_pack_root), first_snapshot)

    def test_run_resume_revalidates_outputs_without_stage_evidence(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, _run_dir = _prepare_source_pack_run(tempdir)
            evidence = _write_offline_stage_evidence(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            classification_path = _write_classification_evidence_json(tempdir)
            full_args = _full_run_args(
                source_pack_root=source_pack_root,
                evidence=evidence,
                handoff_path=handoff_path,
                classification_path=classification_path,
            )
            self.assertEqual(run_stage_cli(full_args, stdout=StringIO()), 0)
            before = _snapshot_files(source_pack_root)

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
                    (
                        "extract-native,extract-ocr,route,structure,summarize,"
                        "card,index,acceptance,classify,writeback"
                    ),
                    "--resume",
                    "--json",
                ],
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["resumed"], payload["stages"])
            self.assertEqual(_snapshot_files(source_pack_root), before)

    def test_run_rejects_out_of_order_stages_before_writing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            classification_path = _write_classification_evidence_json(tempdir)
            acceptance_path = run_dir / ACCEPTANCE_SUMMARY_REF
            self.assertFalse(acceptance_path.exists())

            stderr = StringIO()
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
                    "classify,acceptance",
                    "--handoff",
                    str(handoff_path),
                    "--classification-evidence",
                    str(classification_path),
                ],
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("canonical pipeline order", stderr.getvalue())
            self.assertFalse(acceptance_path.exists())

    def test_classification_failure_does_not_leave_partial_preview(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, run_dir = _prepare_fixture_run(tempdir)
            handoff_path = _write_handoff_jsonl(tempdir)
            self.assertEqual(
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
                    ],
                    stdout=StringIO(),
                ),
                0,
            )
            classification_path = _write_classification_evidence_json(tempdir)
            payload = json.loads(classification_path.read_text(encoding="utf-8"))
            payload["evidence_refs"] = ["missing-evidence.json"]
            classification_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            stderr = StringIO()
            exit_code = run_stage_cli(
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
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("does not exist", stderr.getvalue())
            self.assertFalse((run_dir / WRITEBACK_PREVIEW_REF).exists())
            self.assertFalse((run_dir / CLASSIFICATION_PLAN_REF).exists())

    def test_run_identity_drift_blocks_retrieval(self):
        drift_cases = (
            ("run_id", "artifact index run_id drift"),
            ("source_hash", "artifact index source_hash drift"),
            ("source_identity", "artifact index canonical_filename drift"),
            ("stage_status", "stage status drift"),
            ("stage_set", "stage set drift"),
        )
        for drift_case, expected_error in drift_cases:
            with (
                self.subTest(drift_case=drift_case),
                tempfile.TemporaryDirectory() as tempdir,
            ):
                source_pack_root, run_dir = _prepare_fixture_run(tempdir)
                artifact_index_path = run_dir / "artifact-index.json"
                payload = json.loads(artifact_index_path.read_text(encoding="utf-8"))
                if drift_case == "run_id":
                    payload["run_id"] = "wrong-run"
                elif drift_case == "source_hash":
                    payload["source_pack"]["source_hash"] = "sha256:" + ("0" * 64)
                elif drift_case == "source_identity":
                    payload["source_identity"]["canonical_filename"] = "other.pdf"
                elif drift_case == "stage_status":
                    payload["stages"]["summarize"]["status"] = "failed"
                else:
                    payload["stages"].pop("summarize")
                artifact_index_path.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                stderr = StringIO()
                exit_code = run_stage_cli(
                    [
                        "retrieve",
                        "--source-pack-root",
                        str(source_pack_root),
                        "--paper-id",
                        PAPER_ID,
                        "--run-id",
                        RUN_ID,
                    ],
                    stderr=stderr,
                )

                self.assertEqual(exit_code, 2)
                self.assertIn(expected_error, stderr.getvalue())

    def test_live_modes_stop_at_manual_gate(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root, _run_dir = _prepare_fixture_run(tempdir)
            stderr = StringIO()

            exit_code = run_stage_cli(
                [
                    "writeback",
                    "--source-pack-root",
                    str(source_pack_root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                    "--mode",
                    "approved-live",
                ],
                stderr=stderr,
            )

            self.assertEqual(exit_code, 3)
            self.assertIn("separate manual approval", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
