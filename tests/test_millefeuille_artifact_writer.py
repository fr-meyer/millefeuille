"""Tests for dry-run artifact-index and stage-manifest writing."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
