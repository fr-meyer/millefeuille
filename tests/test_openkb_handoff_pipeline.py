"""Tests for standalone OpenKB handoff pipeline behavior and parent version semantics."""

import unittest
from unittest.mock import MagicMock, patch

from zotero_docai_pipeline.cli.main import validate_flags
from zotero_docai_pipeline.clients.zotero_client import ZoteroClient
from zotero_docai_pipeline.domain.config import (
    AppConfig,
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
    TagRuleConfig,
    TagSelectionConfig,
    TagTargetConfig,
    TaggingConfig,
    TreeStructureConfig,
    ZoteroConfig,
)
from zotero_docai_pipeline.domain.models import (
    AttachmentInfo,
    DiscoveredItem,
    DiscoveryStats,
    OpenKBHandoffRow,
    PaperMetadata,
)
from zotero_docai_pipeline.orchestration.pipeline import Pipeline
from zotero_docai_pipeline.utils.export import (
    ValidationReport,
    build_openkb_handoff_rows,
)


def _make_client():
    """Build a ZoteroClient without running __init__."""
    client = object.__new__(ZoteroClient)
    client._zotero_read = MagicMock()
    stub_config = MagicMock()
    stub_config.library_id = "123456"
    client.config = stub_config
    client.credentials = stub_config
    return client, client._zotero_read


def _make_discovery_stats():
    return DiscoveryStats(matched_count=1, excluded_count=0, excluded_by_rule={})


def _make_item(key="ITEM1", title="Paper", attachments=None):
    return DiscoveredItem(
        key=key,
        title=title,
        tags=[],
        attachments=attachments or [],
        citation_key=None,
        paper_metadata=PaperMetadata(),
    )


def _make_default_outcome_tagging_config() -> TaggingConfig:
    """Tagging config matching packaged defaults (non-empty outcome tags)."""
    return TaggingConfig(
        selection=TagSelectionConfig(
            include=TagRuleConfig(values=["docai"]),
            exclude=TagRuleConfig(values=["docai-processed"]),
        ),
        apply_on_success=TagTargetConfig(values=["docai-processed"]),
        apply_on_error=TagTargetConfig(values=["docai-error"]),
    )


def _make_standalone_handoff_app_config(*, dry_run: bool) -> AppConfig:
    """Standalone OpenKB handoff with default outcome tags and no write key."""
    return AppConfig(
        zotero=ZoteroConfig(),
        ocr=MistralOCRConfig(enabled=False),
        processing=ProcessingConfig(dry_run=dry_run),
        storage=StorageConfig(),
        credentials=AuthQueryConfig(
            library_id="123456",
            read_key="read-key",
            write_key=None,
        ),
        download=DownloadConfig(enabled=False),
        tag_adding=TagAddingConfig(enabled=False),
        selection_tagging=SelectionTaggingConfig(enabled=False),
        tagging=_make_default_outcome_tagging_config(),
        export=ExportConfig(
            openkb_handoff=OpenKBHandoffExportConfig(
                enabled=True,
                jsonl_path="./handoff.jsonl",
            )
        ),
    )


def _make_handoff_row():
    recovery = {
        "method": "zotero_api_attachment",
        "library_id": "123456",
        "library_type": "user",
        "item_key": "ITEM1",
        "attachment_key": "ATT1",
    }
    openkb_policy_hints = {
        "no_auth_url": True,
        "prefer_canonical_filename": True,
        "verification_required": True,
        "source_type": "zotero",
    }
    return OpenKBHandoffRow(
        schema_version="openkb-docai-handoff/v0.1",
        source_type="zotero",
        discovered_at="2024-01-01T00:00:00+00:00",
        item_key="ITEM1",
        attachment_key="ATT1",
        canonical_filename="Smith - 2024 - Title.pdf",
        is_pdf=True,
        verification_strength="key-only",
        recovery=recovery,
        openkb_policy_hints=openkb_policy_hints,
    )


class TestStandaloneOpenkbHandoffPipeline(unittest.TestCase):
    """Standalone live handoff export must not enter OCR/PDF phases."""

    def _make_standalone_pipeline(self):
        pipeline = object.__new__(Pipeline)
        pipeline.logger = MagicMock()
        pipeline.processing_config = ProcessingConfig()
        pipeline.tree_processor = None
        pipeline.tree_structure_config = TreeStructureConfig()
        pipeline.download_config = DownloadConfig(enabled=False)
        pipeline.tag_adding_config = TagAddingConfig(enabled=False)
        pipeline.selection_tagging_config = SelectionTaggingConfig(enabled=False)
        pipeline.ocr_config = MistralOCRConfig(enabled=False)
        pipeline.export_config = ExportConfig(
            openkb_handoff=OpenKBHandoffExportConfig(
                enabled=True,
                jsonl_path="./handoff.jsonl",
            )
        )
        pipeline.zotero_client = MagicMock()
        return pipeline

    def test_standalone_handoff_skips_pdf_and_ocr_phases(self):
        pipeline = self._make_standalone_pipeline()
        item = _make_item()
        discovery_stats = _make_discovery_stats()
        clean_report = ValidationReport(total_rows=1, clean_rows=1, failures=[])

        with (
            patch.object(
                pipeline,
                "_discover_items",
                return_value=([item], discovery_stats),
            ),
            patch.object(pipeline, "_collect_all_pdfs") as mock_collect,
            patch.object(pipeline, "_upload_pdfs_batch") as mock_upload,
            patch.object(pipeline, "_poll_ocr_results_batch") as mock_poll,
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.build_openkb_handoff_rows",
                return_value=[_make_handoff_row()],
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.validate_openkb_handoff_rows",
                return_value=clean_report,
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.write_openkb_jsonl",
            ),
        ):
            summary = pipeline.run()

        mock_collect.assert_not_called()
        mock_upload.assert_not_called()
        mock_poll.assert_not_called()
        self.assertEqual(summary["openkb_handoff_rows_written"], 1)
        self.assertEqual(summary["total_pdfs_processed"], 0)
        self.assertEqual(summary["results"], [])
        self.assertEqual(summary["successful_items"], 1)

        info_messages = [
            str(c.args[0]) for c in pipeline.logger.info.call_args_list if c.args
        ]
        self.assertTrue(
            any("Standalone OpenKB handoff mode" in msg for msg in info_messages)
        )


class TestStandaloneOpenkbHandoffValidateFlags(unittest.TestCase):
    """Standalone handoff must not require ZOTERO_WRITE_KEY for default outcome tags."""

    def test_live_standalone_handoff_without_write_key_passes_validation(self):
        cfg = _make_standalone_handoff_app_config(dry_run=False)
        try:
            validate_flags(cfg)
        except ConfigError:
            self.fail("validate_flags raised ConfigError unexpectedly")

    def test_dry_run_standalone_handoff_without_write_key_passes_validation(self):
        cfg = _make_standalone_handoff_app_config(dry_run=True)
        try:
            validate_flags(cfg)
        except ConfigError:
            self.fail("validate_flags raised ConfigError unexpectedly")


class TestOpenkbHandoffParentZoteroVersion(unittest.TestCase):
    """Handoff rows must use the parent bibliographic item version."""

    def test_parent_zotero_version_not_attachment_child(self):
        client, mock_zr = _make_client()
        parent_version = 42
        child_version = 99
        item_data = {
            "key": "ITEM1",
            "title": "A Paper",
            "tags": [],
            "version": parent_version,
            "itemType": "journalArticle",
        }

        def fetch_side_effect(tag):
            return {"ITEM1": item_data}

        mock_zr.children.return_value = [
            {
                "key": "ATT1",
                "data": {
                    "filename": "Smith - 2024 - Title.pdf",
                    "contentType": "application/pdf",
                    "linkMode": "imported_file",
                    "version": child_version,
                },
            }
        ]

        selection = TagSelectionConfig(
            include=TagRuleConfig(values=["docai"], operator="or"),
        )
        with patch.object(
            client, "_fetch_items_for_tag", side_effect=fetch_side_effect
        ):
            discovered, _ = client.get_items_by_selection(
                selection, include_abstract=False
            )

        self.assertEqual(len(discovered), 1)
        attachment = discovered[0].attachments[0]
        self.assertIsInstance(attachment, AttachmentInfo)
        self.assertEqual(attachment.zotero_version, parent_version)
        self.assertNotEqual(attachment.zotero_version, child_version)

        rows = build_openkb_handoff_rows(
            discovered,
            client,
            OpenKBHandoffExportConfig(enabled=True, include_zotero_version=True),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].zotero_version, parent_version)


if __name__ == "__main__":
    unittest.main()
