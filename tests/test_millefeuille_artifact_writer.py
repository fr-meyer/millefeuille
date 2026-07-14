"""Tests for dry-run artifact-index and stage-manifest writing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from millefeuille.cli.commands import dry_run_command
from millefeuille.cli.main import _translate_artifact_writer_args, validate_flags
from millefeuille.domain.artifacts import load_artifact_index
from millefeuille.domain.config import (
    AppConfig,
    ArtifactExportConfig,
    AuthQueryConfig,
    ConfigError,
    DownloadConfig,
    ExportConfig,
    MistralOCRConfig,
    OpenKBHandoffExportConfig,
    ProcessingConfig,
    SelectionTaggingConfig,
    StorageConfig,
    TagAddingConfig,
    TaggingConfig,
    TagRuleConfig,
    TagSelectionConfig,
    TreeStructureConfig,
    ZoteroConfig,
)
from millefeuille.domain.models import (
    AttachmentInfo,
    DiscoveredItem,
    DiscoveryStats,
    PaperMetadata,
)
from millefeuille.domain.source_packs import (
    RecoveredPdfEvidence,
    write_source_pack_from_recovered_pdf,
)

FIXTURE_PDF_BYTES = b"artifact writer source-pack fixture pdf bytes\n"
FIXTURE_PDF_SHA256 = hashlib.sha256(FIXTURE_PDF_BYTES).hexdigest()


def _make_app_config(**overrides) -> AppConfig:
    return AppConfig(
        zotero=ZoteroConfig(),
        ocr=overrides.pop("ocr", MistralOCRConfig(enabled=False)),
        processing=overrides.pop("processing", ProcessingConfig(dry_run=True)),
        storage=StorageConfig(),
        credentials=overrides.pop(
            "credentials",
            AuthQueryConfig(
                library_id="123",
                read_key="read-key",
                write_key=None,
            ),
        ),
        tree_structure=TreeStructureConfig(),
        download=DownloadConfig(enabled=False),
        tag_adding=TagAddingConfig(enabled=False),
        tagging=TaggingConfig(
            selection=TagSelectionConfig(
                include=TagRuleConfig(values=["millefeuille"])
            )
        ),
        selection_tagging=SelectionTaggingConfig(enabled=False),
        **overrides,
    )


def _make_discovery_stats() -> DiscoveryStats:
    return DiscoveryStats(matched_count=1, excluded_count=0, excluded_by_rule={})


def _write_recovered_pdf(tempdir: str) -> Path:
    source_path = Path(tempdir) / "recovered.pdf"
    source_path.write_bytes(FIXTURE_PDF_BYTES)
    return source_path


def _write_markdown(tempdir: str, filename: str, text: str) -> Path:
    markdown_path = Path(tempdir) / filename
    markdown_path.write_text(text, encoding="utf-8")
    return markdown_path


def _write_native_extraction_evidence_json(
    tempdir: str,
    markdown_path: Path,
) -> Path:
    evidence_path = Path(tempdir) / "native-extraction-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": (
                    "millefeuille-native-extraction-evidence/v0.1"
                ),
                "source_type": "zotero",
                "item_key": "ITEM1",
                "attachment_key": "ATT1",
                "canonical_filename": (
                    "Example Author - 2026 - Artifact Writer.pdf"
                ),
                "markdown_path": markdown_path.name,
                "expected_sha256": FIXTURE_PDF_SHA256,
                "page_count": 2,
                "tool": "PyPDF2-fixture",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_ocr_extraction_evidence_json(
    tempdir: str,
    markdown_path: Path,
) -> Path:
    evidence_path = Path(tempdir) / "ocr-extraction-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "millefeuille-ocr-extraction-evidence/v0.1",
                "source_type": "zotero",
                "item_key": "ITEM1",
                "attachment_key": "ATT1",
                "canonical_filename": (
                    "Example Author - 2026 - Artifact Writer.pdf"
                ),
                "markdown_path": markdown_path.name,
                "expected_sha256": FIXTURE_PDF_SHA256,
                "page_count": 2,
                "provider": "mistral-ocr",
                "requested_model": "mistral-ocr-latest",
                "provider_version": "mistral-ocr-4-0-fixture",
                "provider_payload_disposition": "discarded",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


def _write_source_pack(tempdir: str) -> Path:
    source_path = _write_recovered_pdf(tempdir)
    source_pack_root = Path(tempdir) / "source-packs"
    evidence = RecoveredPdfEvidence(
        item_key="ITEM1",
        attachment_key="ATT1",
        canonical_filename="Example Author - 2026 - Artifact Writer.pdf",
        recovered_pdf_path=source_path,
        expected_sha256=FIXTURE_PDF_SHA256,
        file_size_bytes=len(FIXTURE_PDF_BYTES),
        zotero_version=7,
    )
    write_source_pack_from_recovered_pdf(
        evidence=evidence,
        source_pack_root=source_pack_root,
        created_at="2026-07-14T03:00:00+00:00",
    )
    return source_pack_root


def _make_item(
    *,
    key: str = "ITEM1",
    attachment_key: str = "ATT1",
    sha256: str = "c" * 64,
) -> DiscoveredItem:
    attachment = AttachmentInfo(
        key=attachment_key,
        filename="Example Author - 2026 - Artifact Writer.pdf",
        content_type="application/pdf",
        link_mode="imported_file",
        file_size_bytes=12345,
        sha256=sha256,
        zotero_version=7,
        item_type="journalArticle",
    )
    metadata = PaperMetadata(
        title="Artifact Writer Paper",
        year=2026,
        doi="10.0000/artifact-writer",
    )
    return DiscoveredItem(
        key=key,
        title="Artifact Writer Paper",
        tags=["millefeuille"],
        attachments=[attachment],
        citation_key="artifact2026writer",
        paper_metadata=metadata,
    )


def _make_multi_pdf_item() -> DiscoveredItem:
    attachment_one = AttachmentInfo(
        key="ATT1",
        filename="Example Author - 2026 - Main paper.pdf",
        content_type="application/pdf",
        link_mode="imported_file",
        file_size_bytes=12345,
        sha256="a" * 64,
        zotero_version=7,
        item_type="journalArticle",
    )
    attachment_two = AttachmentInfo(
        key="ATT2",
        filename="Example Author - 2026 - Supplement.pdf",
        content_type="application/pdf",
        link_mode="imported_file",
        file_size_bytes=23456,
        sha256="b" * 64,
        zotero_version=7,
        item_type="journalArticle",
    )
    metadata = PaperMetadata(
        title="Artifact Writer Paper",
        year=2026,
        doi="10.0000/artifact-writer",
    )
    return DiscoveredItem(
        key="ITEM1",
        title="Artifact Writer Paper",
        tags=["millefeuille"],
        attachments=[attachment_one, attachment_two],
        citation_key="artifact2026writer",
        paper_metadata=metadata,
    )


class TestDryRunArtifactWriter(unittest.TestCase):
    def test_dry_run_writes_artifact_index_and_stage_manifest(self):
        with tempfile.TemporaryDirectory() as tempdir:
            cfg = _make_app_config(
                export=ExportConfig(
                    openkb_handoff=OpenKBHandoffExportConfig(enabled=True),
                    artifacts=ArtifactExportConfig(
                        enabled=True,
                        artifact_root=tempdir,
                        run_id="run-fixture",
                    ),
                )
            )
            logger = MagicMock()
            mock_zotero = MagicMock()
            mock_zotero.credentials = SimpleNamespace(library_id="123")
            mock_zotero.get_items_by_selection.return_value = (
                [_make_item()],
                _make_discovery_stats(),
            )

            exit_code = dry_run_command(cfg, logger, mock_zotero)

            run_dir = Path(tempdir) / "zotero-ITEM1" / "run-fixture"
            index_path = run_dir / "artifact-index.json"
            stage_manifest_path = run_dir / "stage-manifest.json"

            self.assertEqual(exit_code, 0)
            self.assertTrue(index_path.is_file())
            self.assertTrue(stage_manifest_path.is_file())
            mock_zotero.download_pdf.assert_not_called()
            mock_zotero.add_tag.assert_not_called()
            mock_zotero.remove_tag.assert_not_called()

            artifact_index = load_artifact_index(index_path)
            self.assertEqual(artifact_index.paper_id, "zotero-ITEM1")
            self.assertEqual(artifact_index.run_id, "run-fixture")
            self.assertEqual(
                artifact_index.source_pack["source_hash"],
                "sha256:" + ("c" * 64),
            )
            self.assertEqual(
                artifact_index.source_identity["zotero_attachment_key"],
                "ATT1",
            )
            self.assertEqual(
                artifact_index.stages["source-pack"]["status"],
                "manual-gate",
            )
            self.assertIn(
                "stage source-pack: manual-gate",
                artifact_index.status().blocking_items,
            )

            stage_manifest = json.loads(
                stage_manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                stage_manifest["schema_version"],
                "millefeuille-stage-manifest/v0.1",
            )
            self.assertEqual(stage_manifest["mode"], "preview")
            self.assertIn("pdf_recovery", stage_manifest["manual_gates"])
            self.assertIn("source_pack_write", stage_manifest["manual_gates"])

    def test_source_pack_artifact_root_preflights_before_writing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            existing_source_pack_dir = Path(tempdir) / "zotero" / "zotero-ITEM1"
            existing_source_pack_dir.mkdir(parents=True)
            (existing_source_pack_dir / "manifest.json").write_text(
                '{"schema_version":"source-pack-fixture/v0.1","source_hash":"sha256:'
                + ("c" * 64)
                + '"}\n',
                encoding="utf-8",
            )
            cfg = _make_app_config(
                export=ExportConfig(
                    openkb_handoff=OpenKBHandoffExportConfig(enabled=True),
                    artifacts=ArtifactExportConfig(
                        enabled=True,
                        artifact_root="source-pack",
                        source_pack_root=tempdir,
                        run_id="run-fixture",
                    ),
                )
            )
            logger = MagicMock()
            mock_zotero = MagicMock()
            mock_zotero.credentials = SimpleNamespace(library_id="123")
            mock_zotero.get_items_by_selection.return_value = (
                [
                    _make_item(),
                    _make_item(
                        key="ITEM2",
                        attachment_key="ATT2",
                        sha256="d" * 64,
                    ),
                ],
                _make_discovery_stats(),
            )

            with self.assertRaisesRegex(ValueError, "zotero-ITEM2/manifest.json"):
                dry_run_command(cfg, logger, mock_zotero)

            self.assertFalse((existing_source_pack_dir / "analyses").exists())
            mock_zotero.download_pdf.assert_not_called()
            mock_zotero.add_tag.assert_not_called()
            mock_zotero.remove_tag.assert_not_called()

    def test_source_pack_artifact_root_writes_under_existing_source_pack(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_dir = Path(tempdir) / "zotero" / "zotero-ITEM1"
            source_pack_dir.mkdir(parents=True)
            (source_pack_dir / "manifest.json").write_text(
                '{"schema_version":"source-pack-fixture/v0.1","source_hash":"sha256:'
                + ("c" * 64)
                + '"}\n',
                encoding="utf-8",
            )
            cfg = _make_app_config(
                export=ExportConfig(
                    openkb_handoff=OpenKBHandoffExportConfig(enabled=True),
                    artifacts=ArtifactExportConfig(
                        enabled=True,
                        artifact_root="source-pack",
                        source_pack_root=tempdir,
                        run_id="run-fixture",
                    ),
                )
            )
            logger = MagicMock()
            mock_zotero = MagicMock()
            mock_zotero.credentials = SimpleNamespace(library_id="123")
            mock_zotero.get_items_by_selection.return_value = (
                [_make_item()],
                _make_discovery_stats(),
            )

            exit_code = dry_run_command(cfg, logger, mock_zotero)

            run_dir = source_pack_dir / "analyses" / "millefeuille" / "run-fixture"
            index_path = run_dir / "artifact-index.json"
            stage_manifest_path = run_dir / "stage-manifest.json"

            self.assertEqual(exit_code, 0)
            self.assertTrue(index_path.is_file())
            self.assertTrue(stage_manifest_path.is_file())
            mock_zotero.download_pdf.assert_not_called()
            mock_zotero.add_tag.assert_not_called()
            mock_zotero.remove_tag.assert_not_called()

            artifact_index = load_artifact_index(index_path)
            self.assertEqual(artifact_index.paper_id, "zotero-ITEM1")
            self.assertEqual(artifact_index.artifact_root, str(run_dir))
            self.assertEqual(artifact_index.source_pack["ref"], str(source_pack_dir))
            self.assertEqual(
                artifact_index.source_pack["manifest_ref"],
                "../../../manifest.json",
            )
            self.assertEqual(
                artifact_index.stages["source-pack"]["status"],
                "passed",
            )
            self.assertNotIn(
                "stage source-pack: manual-gate",
                artifact_index.status().blocking_items,
            )

            stage_manifest = json.loads(
                stage_manifest_path.read_text(encoding="utf-8")
            )
            self.assertNotIn("pdf_recovery", stage_manifest["manual_gates"])
            self.assertNotIn("source_pack_write", stage_manifest["manual_gates"])
            source_pack_stage = next(
                stage
                for stage in stage_manifest["stages"]
                if stage["name"] == "source-pack"
            )
            self.assertEqual(source_pack_stage["status"], "passed")

    def test_source_pack_intake_fixture_path_feeds_artifact_writer(self):
        with tempfile.TemporaryDirectory() as tempdir:
            fixture_bytes = b"x" * 12345
            fixture_sha256 = (
                "3c49a9d347ea48dc66e4f50b991829cdbef079a2e57c805cc0163f6b"
                "18860fb1"
            )
            recovered_pdf = Path(tempdir) / "recovered.pdf"
            recovered_pdf.write_bytes(fixture_bytes)
            evidence_path = Path(tempdir) / "recovered-pdf-evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "schema_version": "millefeuille-recovered-pdf-evidence/v0.1",
                        "source_type": "zotero",
                        "item_key": "ITEM1",
                        "attachment_key": "ATT1",
                        "canonical_filename": (
                            "Example Author - 2026 - Artifact Writer.pdf"
                        ),
                        "recovered_pdf_path": recovered_pdf.name,
                        "expected_sha256": fixture_sha256,
                        "content_type": "application/pdf",
                        "file_size_bytes": len(fixture_bytes),
                        "zotero_version": 7,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            source_pack_root = Path(tempdir) / "source-packs"
            cfg = _make_app_config(
                export=ExportConfig(
                    openkb_handoff=OpenKBHandoffExportConfig(enabled=True),
                    artifacts=ArtifactExportConfig(
                        enabled=True,
                        artifact_root="source-pack",
                        source_pack_root=str(source_pack_root),
                        source_pack_intake_evidence_path=str(evidence_path),
                        run_id="run-fixture",
                    ),
                )
            )
            logger = MagicMock()
            mock_zotero = MagicMock()
            mock_zotero.credentials = SimpleNamespace(library_id="123")
            mock_zotero.get_items_by_selection.return_value = (
                [_make_item(sha256=fixture_sha256)],
                _make_discovery_stats(),
            )

            exit_code = dry_run_command(cfg, logger, mock_zotero)

            source_pack_dir = source_pack_root / "zotero" / "zotero-ITEM1"
            run_dir = source_pack_dir / "analyses" / "millefeuille" / "run-fixture"
            index_path = run_dir / "artifact-index.json"

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                (source_pack_dir / "source.pdf").read_bytes(),
                fixture_bytes,
            )
            self.assertTrue((source_pack_dir / "manifest.json").is_file())
            self.assertTrue(index_path.is_file())
            mock_zotero.download_pdf.assert_not_called()
            mock_zotero.add_tag.assert_not_called()
            mock_zotero.remove_tag.assert_not_called()

            artifact_index = load_artifact_index(index_path)
            self.assertEqual(
                artifact_index.source_pack["source_hash"],
                f"sha256:{fixture_sha256}",
            )
            self.assertEqual(
                artifact_index.stages["source-pack"]["status"],
                "passed",
            )

    def test_extraction_fixture_paths_feed_source_pack_artifact_writer(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_pack_root = _write_source_pack(tempdir)
            native_markdown_path = _write_markdown(
                tempdir,
                "native-fulltext.md",
                "Native fixture page 1\nNative fixture page 2\n",
            )
            ocr_markdown_path = _write_markdown(
                tempdir,
                "ocr-fulltext.md",
                "OCR fixture page 1\nOCR fixture page 2\n",
            )
            native_evidence_path = _write_native_extraction_evidence_json(
                tempdir,
                native_markdown_path,
            )
            ocr_evidence_path = _write_ocr_extraction_evidence_json(
                tempdir,
                ocr_markdown_path,
            )
            cfg = _make_app_config(
                export=ExportConfig(
                    artifacts=ArtifactExportConfig(
                        enabled=True,
                        artifact_root="source-pack",
                        source_pack_root=str(source_pack_root),
                        native_extraction_evidence_path=str(native_evidence_path),
                        ocr_extraction_evidence_path=str(ocr_evidence_path),
                        run_id="run-fixture",
                    )
                )
            )
            logger = MagicMock()
            mock_zotero = MagicMock()
            mock_zotero.credentials = SimpleNamespace(library_id="123")
            mock_zotero.get_items_by_selection.return_value = (
                [_make_item(sha256=FIXTURE_PDF_SHA256)],
                _make_discovery_stats(),
            )

            exit_code = dry_run_command(cfg, logger, mock_zotero)

            run_dir = (
                source_pack_root
                / "zotero"
                / "zotero-ITEM1"
                / "analyses"
                / "millefeuille"
                / "run-fixture"
            )
            artifact_index = load_artifact_index(run_dir / "artifact-index.json")

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                artifact_index.stages["extract-native"]["status"],
                "passed",
            )
            self.assertEqual(
                artifact_index.stages["extract-ocr"]["status"],
                "passed",
            )
            self.assertIn(
                "native_extraction_evidence",
                artifact_index.artifacts,
            )
            self.assertIn(
                "ocr_extraction_evidence",
                artifact_index.artifacts,
            )
            self.assertTrue(
                (
                    source_pack_root
                    / "zotero"
                    / "zotero-ITEM1"
                    / "extractions"
                    / "native"
                    / "evidence.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    source_pack_root
                    / "zotero"
                    / "zotero-ITEM1"
                    / "extractions"
                    / "mistral-ocr"
                    / "evidence.json"
                ).is_file()
            )
            mock_zotero.download_pdf.assert_not_called()
            mock_zotero.add_tag.assert_not_called()
            mock_zotero.remove_tag.assert_not_called()

    def test_source_pack_intake_fixture_path_rejects_multi_pdf_item_cleanly(self):
        with tempfile.TemporaryDirectory() as tempdir:
            recovered_main = Path(tempdir) / "main.pdf"
            recovered_supplement = Path(tempdir) / "supplement.pdf"
            main_bytes = b"main pdf bytes\n"
            supplement_bytes = b"supplement pdf bytes\n"
            recovered_main.write_bytes(main_bytes)
            recovered_supplement.write_bytes(supplement_bytes)
            evidence_path = Path(tempdir) / "recovered-pdf-evidence.jsonl"
            evidence_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "schema_version": (
                                    "millefeuille-recovered-pdf-evidence/v0.1"
                                ),
                                "source_type": "zotero",
                                "item_key": "ITEM1",
                                "attachment_key": "ATT1",
                                "canonical_filename": (
                                    "Example Author - 2026 - Main paper.pdf"
                                ),
                                "recovered_pdf_path": recovered_main.name,
                                "expected_sha256": "a" * 64,
                                "content_type": "application/pdf",
                                "file_size_bytes": len(main_bytes),
                                "zotero_version": 7,
                            }
                        ),
                        json.dumps(
                            {
                                "schema_version": (
                                    "millefeuille-recovered-pdf-evidence/v0.1"
                                ),
                                "source_type": "zotero",
                                "item_key": "ITEM1",
                                "attachment_key": "ATT2",
                                "canonical_filename": (
                                    "Example Author - 2026 - Supplement.pdf"
                                ),
                                "recovered_pdf_path": recovered_supplement.name,
                                "expected_sha256": "b" * 64,
                                "content_type": "application/pdf",
                                "file_size_bytes": len(supplement_bytes),
                                "zotero_version": 7,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            source_pack_root = Path(tempdir) / "source-packs"
            cfg = _make_app_config(
                export=ExportConfig(
                    openkb_handoff=OpenKBHandoffExportConfig(enabled=True),
                    artifacts=ArtifactExportConfig(
                        enabled=True,
                        artifact_root="source-pack",
                        source_pack_root=str(source_pack_root),
                        source_pack_intake_evidence_path=str(evidence_path),
                        run_id="run-fixture",
                    ),
                )
            )
            logger = MagicMock()
            mock_zotero = MagicMock()
            mock_zotero.credentials = SimpleNamespace(library_id="123")
            mock_zotero.get_items_by_selection.return_value = (
                [_make_multi_pdf_item()],
                _make_discovery_stats(),
            )

            with self.assertRaisesRegex(
                ValueError,
                "exactly one PDF handoff row per Zotero item/source pack",
            ):
                dry_run_command(cfg, logger, mock_zotero)

            self.assertFalse((source_pack_root / "zotero").exists())
            mock_zotero.download_pdf.assert_not_called()
            mock_zotero.add_tag.assert_not_called()
            mock_zotero.remove_tag.assert_not_called()

    def test_source_pack_artifact_root_requires_existing_manifest(self):
        with tempfile.TemporaryDirectory() as tempdir:
            cfg = _make_app_config(
                export=ExportConfig(
                    openkb_handoff=OpenKBHandoffExportConfig(enabled=True),
                    artifacts=ArtifactExportConfig(
                        enabled=True,
                        artifact_root="source-pack",
                        source_pack_root=tempdir,
                        run_id="run-fixture",
                    ),
                )
            )
            logger = MagicMock()
            mock_zotero = MagicMock()
            mock_zotero.credentials = SimpleNamespace(library_id="123")
            mock_zotero.get_items_by_selection.return_value = (
                [_make_item()],
                _make_discovery_stats(),
            )

            with self.assertRaisesRegex(ValueError, "source-pack manifest"):
                dry_run_command(cfg, logger, mock_zotero)

            mock_zotero.download_pdf.assert_not_called()
            mock_zotero.add_tag.assert_not_called()
            mock_zotero.remove_tag.assert_not_called()

    def test_validate_flags_rejects_live_artifact_writes(self):
        cfg = _make_app_config(
            processing=ProcessingConfig(dry_run=False),
            export=ExportConfig(
                artifacts=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="/tmp/millefeuille-artifacts",
                )
            ),
        )

        with self.assertRaisesRegex(ConfigError, "processing.dry_run=true"):
            validate_flags(cfg)

    def test_validate_flags_accepts_source_pack_root_in_dry_run(self):
        cfg = _make_app_config(
            export=ExportConfig(
                artifacts=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="source-pack",
                    source_pack_root="/tmp/millefeuille-source-packs",
                )
            )
        )

        validate_flags(cfg)

    def test_validate_flags_accepts_extraction_fixture_paths(self):
        cfg = _make_app_config(
            export=ExportConfig(
                artifacts=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="source-pack",
                    source_pack_root="/tmp/millefeuille-source-packs",
                    native_extraction_evidence_path="/tmp/native.json",
                    ocr_extraction_evidence_path="/tmp/ocr.json",
                )
            )
        )

        validate_flags(cfg)

    def test_validate_flags_rejects_source_pack_intake_without_handoff(self):
        cfg = _make_app_config(
            export=ExportConfig(
                artifacts=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="source-pack",
                    source_pack_root="/tmp/millefeuille-source-packs",
                    source_pack_intake_evidence_path="/tmp/evidence.json",
                )
            )
        )

        with self.assertRaisesRegex(ConfigError, "openkb_handoff.enabled=true"):
            validate_flags(cfg)

    def test_validate_flags_rejects_source_pack_intake_without_root(self):
        cfg = _make_app_config(
            export=ExportConfig(
                openkb_handoff=OpenKBHandoffExportConfig(enabled=True),
                artifacts=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="source-pack",
                    source_pack_intake_evidence_path="/tmp/evidence.json",
                ),
            )
        )

        with self.assertRaisesRegex(ConfigError, "source_pack_root"):
            validate_flags(cfg)

    def test_validate_flags_rejects_extraction_fixture_without_root(self):
        cfg = _make_app_config(
            export=ExportConfig(
                artifacts=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="source-pack",
                    native_extraction_evidence_path="/tmp/native.json",
                )
            )
        )

        with self.assertRaisesRegex(ConfigError, "source_pack_root"):
            validate_flags(cfg)


class TestArtifactWriterCliAliases(unittest.TestCase):
    def test_artifact_root_alias_enables_artifact_export(self):
        translated = _translate_artifact_writer_args([
            "--artifact-root",
            "/tmp/millefeuille-artifacts",
            "--run-id",
            "run-fixture",
            "processing.dry_run=true",
        ])

        self.assertIn(
            "export.artifacts.artifact_root=/tmp/millefeuille-artifacts",
            translated,
        )
        self.assertIn("export.artifacts.run_id=run-fixture", translated)
        self.assertIn("export.artifacts.enabled=true", translated)
        self.assertIn("processing.dry_run=true", translated)

    def test_artifact_root_equals_form_is_supported(self):
        translated = _translate_artifact_writer_args([
            "--artifact-root=/tmp/mf",
            "--run-id=run-1",
        ])

        self.assertEqual(
            translated,
            [
                "export.artifacts.artifact_root=/tmp/mf",
                "export.artifacts.run_id=run-1",
                "export.artifacts.enabled=true",
            ],
        )

    def test_source_pack_root_alias_is_supported(self):
        translated = _translate_artifact_writer_args([
            "--artifact-root",
            "source-pack",
            "--source-pack-root",
            "/tmp/source-packs",
            "--run-id",
            "run-fixture",
        ])

        self.assertEqual(
            translated,
            [
                "export.artifacts.artifact_root=source-pack",
                "export.artifacts.source_pack_root=/tmp/source-packs",
                "export.artifacts.run_id=run-fixture",
                "export.artifacts.enabled=true",
            ],
        )

    def test_source_pack_intake_evidence_alias_is_supported(self):
        translated = _translate_artifact_writer_args([
            "--artifact-root",
            "source-pack",
            "--source-pack-root",
            "/tmp/source-packs",
            "--source-pack-intake-evidence",
            "/tmp/evidence.jsonl",
            "--run-id",
            "run-fixture",
        ])

        self.assertEqual(
            translated,
            [
                "export.artifacts.artifact_root=source-pack",
                "export.artifacts.source_pack_root=/tmp/source-packs",
                (
                    "export.artifacts.source_pack_intake_evidence_path="
                    "/tmp/evidence.jsonl"
                ),
                "export.artifacts.run_id=run-fixture",
                "export.artifacts.enabled=true",
            ],
        )

    def test_extraction_evidence_aliases_are_supported(self):
        translated = _translate_artifact_writer_args([
            "--artifact-root",
            "source-pack",
            "--source-pack-root",
            "/tmp/source-packs",
            "--native-extraction-evidence",
            "/tmp/native.jsonl",
            "--ocr-extraction-evidence=/tmp/ocr.jsonl",
            "--run-id",
            "run-fixture",
        ])

        self.assertEqual(
            translated,
            [
                "export.artifacts.artifact_root=source-pack",
                "export.artifacts.source_pack_root=/tmp/source-packs",
                (
                    "export.artifacts.native_extraction_evidence_path="
                    "/tmp/native.jsonl"
                ),
                "export.artifacts.ocr_extraction_evidence_path=/tmp/ocr.jsonl",
                "export.artifacts.run_id=run-fixture",
                "export.artifacts.enabled=true",
            ],
        )


if __name__ == "__main__":
    unittest.main()
