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


def _make_item() -> DiscoveredItem:
    attachment = AttachmentInfo(
        key="ATT1",
        filename="Example Author - 2026 - Artifact Writer.pdf",
        content_type="application/pdf",
        link_mode="imported_file",
        file_size_bytes=12345,
        sha256="c" * 64,
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

    def test_validate_flags_rejects_source_pack_root_until_writer_exists(self):
        cfg = _make_app_config(
            export=ExportConfig(
                artifacts=ArtifactExportConfig(
                    enabled=True,
                    artifact_root="source-pack",
                )
            )
        )

        with self.assertRaisesRegex(ConfigError, "source-pack writer"):
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


if __name__ == "__main__":
    unittest.main()
