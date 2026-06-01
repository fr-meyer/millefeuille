"""Tests for standalone OpenKB handoff behavior and parent version semantics."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from zotero_docai_pipeline.cli.commands import dry_run_command
from zotero_docai_pipeline.cli.main import validate_flags
from zotero_docai_pipeline.clients.exceptions import (
    AttachmentIdentityError,
    OpenKBHandoffValidationError,
)
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
    TaggingConfig,
    TagRuleConfig,
    TagSelectionConfig,
    TagTargetConfig,
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
    build_openkb_handoff_preview_rows,
    build_openkb_handoff_rows,
    write_openkb_preview_jsonl,
)


def _weak_row(index: int) -> dict:
    return {
        "item_key": f"ITEM{index}",
        "attachment_key": f"ATT{index}",
        "canonical_filename": f"paper-{index}.pdf",
        "verification_strength": "key-only",
    }


def _make_weak_rows(count: int) -> list[dict]:
    return [_weak_row(i) for i in range(1, count + 1)]


def _format_log_messages(mock_logger, level: str) -> list[str]:
    calls = getattr(mock_logger, level).call_args_list
    messages: list[str] = []
    for call in calls:
        if not call.args:
            continue
        if len(call.args) == 1:
            messages.append(str(call.args[0]))
        else:
            messages.append(call.args[0] % call.args[1:])
    return messages


def _assert_weak_warning_messages(
    test_case: unittest.TestCase,
    logger: MagicMock,
    *,
    weak_rows: list[dict],
) -> None:
    warnings = _format_log_messages(logger, "warning")
    test_case.assertTrue(
        any(
            msg.startswith("[OPENKB HANDOFF]")
            and "weak verification strength" in msg
            and str(len(weak_rows)) in msg
            for msg in warnings
        ),
        f"expected weak-verification header in warnings: {warnings}",
    )
    for entry in weak_rows[:10]:
        test_case.assertTrue(
            any(
                entry["item_key"] in msg
                and entry["attachment_key"] in msg
                and entry["canonical_filename"] in msg
                and entry["verification_strength"] in msg
                for msg in warnings
            ),
            f"expected row detail for {entry['item_key']} in warnings: {warnings}",
        )
    if len(weak_rows) > 10:
        overflow = len(weak_rows) - 10
        test_case.assertTrue(
            any(
                "... and" in msg
                and str(overflow) in msg
                and "weak verification strength" in msg
                for msg in warnings
            ),
            f"expected overflow summary in warnings: {warnings}",
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


class TestOpenkbWeakVerificationWarningLogs(unittest.TestCase):
    """Operator-visible weak-verification warnings in dry-run and live export."""

    def test_dry_run_logs_weak_verification_warnings(self):
        cfg = _make_standalone_handoff_app_config(dry_run=True)
        logger = MagicMock()
        mock_zotero = MagicMock()
        mock_zotero.get_items_by_selection.return_value = (
            [_make_item()],
            _make_discovery_stats(),
        )
        weak_rows = _make_weak_rows(2)
        report = ValidationReport(
            total_rows=2,
            clean_rows=2,
            failures=[],
            weak_verification_rows=weak_rows,
        )

        with (
            patch(
                "zotero_docai_pipeline.cli.commands.build_openkb_handoff_rows",
                return_value=[_make_handoff_row()],
            ),
            patch(
                "zotero_docai_pipeline.cli.commands.validate_openkb_handoff_rows",
                return_value=report,
            ),
        ):
            exit_code = dry_run_command(cfg, logger, mock_zotero)

        self.assertEqual(exit_code, 0)
        _assert_weak_warning_messages(self, logger, weak_rows=weak_rows)

    def test_dry_run_truncates_weak_verification_warnings_after_ten_rows(self):
        cfg = _make_standalone_handoff_app_config(dry_run=True)
        logger = MagicMock()
        mock_zotero = MagicMock()
        mock_zotero.get_items_by_selection.return_value = (
            [_make_item()],
            _make_discovery_stats(),
        )
        weak_rows = _make_weak_rows(11)
        report = ValidationReport(
            total_rows=11,
            clean_rows=11,
            failures=[],
            weak_verification_rows=weak_rows,
        )

        with (
            patch(
                "zotero_docai_pipeline.cli.commands.build_openkb_handoff_rows",
                return_value=[_make_handoff_row()],
            ),
            patch(
                "zotero_docai_pipeline.cli.commands.validate_openkb_handoff_rows",
                return_value=report,
            ),
        ):
            dry_run_command(cfg, logger, mock_zotero)

        detail_warnings = [
            msg
            for msg in _format_log_messages(logger, "warning")
            if msg.startswith("  item_key=")
        ]
        self.assertEqual(len(detail_warnings), 10)
        _assert_weak_warning_messages(self, logger, weak_rows=weak_rows)

    def test_dry_run_logs_weak_warnings_before_failure_exit(self):
        cfg = _make_standalone_handoff_app_config(dry_run=True)
        logger = MagicMock()
        mock_zotero = MagicMock()
        mock_zotero.get_items_by_selection.return_value = (
            [_make_item()],
            _make_discovery_stats(),
        )
        weak_rows = _make_weak_rows(1)
        report = ValidationReport(
            total_rows=2,
            clean_rows=1,
            failures=[AttachmentIdentityError("canonical_filename: duplicate")],
            weak_verification_rows=weak_rows,
        )

        with (
            patch(
                "zotero_docai_pipeline.cli.commands.build_openkb_handoff_rows",
                return_value=[_make_handoff_row()],
            ),
            patch(
                "zotero_docai_pipeline.cli.commands.validate_openkb_handoff_rows",
                return_value=report,
            ),
        ):
            exit_code = dry_run_command(cfg, logger, mock_zotero)

        self.assertEqual(exit_code, 2)
        _assert_weak_warning_messages(self, logger, weak_rows=weak_rows)
        logger.error.assert_called()

    def test_live_export_logs_weak_verification_warnings(self):
        pipeline = TestStandaloneOpenkbHandoffPipeline()._make_standalone_pipeline()
        item = _make_item()
        discovery_stats = _make_discovery_stats()
        weak_rows = _make_weak_rows(2)
        report = ValidationReport(
            total_rows=2,
            clean_rows=2,
            failures=[],
            weak_verification_rows=weak_rows,
        )

        with (
            patch.object(
                pipeline,
                "_discover_items",
                return_value=([item], discovery_stats),
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.build_openkb_handoff_rows",
                return_value=[_make_handoff_row()],
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.validate_openkb_handoff_rows",
                return_value=report,
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.write_openkb_jsonl",
            ),
        ):
            summary = pipeline.run()

        self.assertEqual(summary["openkb_handoff_rows_written"], 1)
        _assert_weak_warning_messages(
            self, pipeline.logger, weak_rows=weak_rows
        )

    def test_live_export_truncates_weak_verification_warnings_after_ten_rows(self):
        pipeline = TestStandaloneOpenkbHandoffPipeline()._make_standalone_pipeline()
        item = _make_item()
        discovery_stats = _make_discovery_stats()
        weak_rows = _make_weak_rows(11)
        report = ValidationReport(
            total_rows=11,
            clean_rows=11,
            failures=[],
            weak_verification_rows=weak_rows,
        )

        with (
            patch.object(
                pipeline,
                "_discover_items",
                return_value=([item], discovery_stats),
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.build_openkb_handoff_rows",
                return_value=[_make_handoff_row()],
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.validate_openkb_handoff_rows",
                return_value=report,
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.write_openkb_jsonl",
            ),
        ):
            pipeline.run()

        detail_warnings = [
            msg
            for msg in _format_log_messages(pipeline.logger, "warning")
            if msg.startswith("  item_key=")
        ]
        self.assertEqual(len(detail_warnings), 10)
        _assert_weak_warning_messages(
            self, pipeline.logger, weak_rows=weak_rows
        )

    def test_live_export_logs_weak_warnings_before_validation_abort(self):
        pipeline = TestStandaloneOpenkbHandoffPipeline()._make_standalone_pipeline()
        item = _make_item()
        discovery_stats = _make_discovery_stats()
        weak_rows = _make_weak_rows(1)
        report = ValidationReport(
            total_rows=2,
            clean_rows=1,
            failures=[AttachmentIdentityError("canonical_filename: duplicate")],
            weak_verification_rows=weak_rows,
        )

        with (
            patch.object(
                pipeline,
                "_discover_items",
                return_value=([item], discovery_stats),
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.build_openkb_handoff_rows",
                return_value=[_make_handoff_row()],
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.validate_openkb_handoff_rows",
                return_value=report,
            ),
            patch(
                "zotero_docai_pipeline.orchestration.pipeline.write_openkb_jsonl",
            ) as mock_write,
            self.assertRaises(OpenKBHandoffValidationError),
        ):
            pipeline.run()

        _assert_weak_warning_messages(
            self, pipeline.logger, weak_rows=weak_rows
        )
        pipeline.logger.error.assert_called()
        mock_write.assert_not_called()


class TestOpenkbHandoffPreviewSerialization(unittest.TestCase):
    """Dry-run preview must not write authoritative handoff rows."""

    def test_preview_rows_are_non_authoritative_and_redacted(self):
        row = _make_handoff_row()
        row.item_title = "Private Paper Title"
        row.citation_key = "PrivateCitation2024"
        row.file_size_bytes = 12345
        row.md5 = "0123456789abcdef"
        row.sha256 = "abcdef0123456789"

        preview = build_openkb_handoff_preview_rows([row])[0]
        live = row.to_dict()

        self.assertNotEqual(preview, live)
        self.assertEqual(
            preview["schema_version"],
            "openkb-docai-handoff-preview/v0.1",
        )
        self.assertTrue(preview["preview"])
        self.assertFalse(preview["authoritative"])
        self.assertNotIn("item_key", preview)
        self.assertNotIn("attachment_key", preview)
        self.assertNotIn("md5", preview)
        self.assertNotIn("sha256", preview)
        self.assertNotIn("citation_key", preview)
        self.assertNotIn("item_title", preview)
        self.assertEqual(
            preview["hashes"],
            {"md5_present": True, "sha256_present": True},
        )
        self.assertNotEqual(preview["identity"]["item_key"], row.item_key)
        self.assertNotEqual(
            preview["identity"]["attachment_key"],
            row.attachment_key,
        )
        self.assertNotIn("item_key", preview["recovery"])
        self.assertNotIn("attachment_key", preview["recovery"])
        self.assertTrue(preview["recovery"]["redacted"])

    def test_preview_writer_serializes_preview_payload(self):
        row = _make_handoff_row()
        row.md5 = "0123456789abcdef"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "handoff.preview.jsonl"
            write_openkb_preview_jsonl([row], str(path))

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            payload["schema_version"],
            "openkb-docai-handoff-preview/v0.1",
        )
        self.assertTrue(payload["preview"])
        self.assertFalse(payload["authoritative"])
        self.assertNotIn("md5", payload)
        self.assertTrue(payload["hashes"]["md5_present"])

    def test_dry_run_preview_writes_preview_payload_not_live_payload(self):
        cfg = _make_standalone_handoff_app_config(dry_run=True)
        logger = MagicMock()
        mock_zotero = MagicMock()
        mock_zotero.get_items_by_selection.return_value = (
            [_make_item()],
            _make_discovery_stats(),
        )
        report = ValidationReport(total_rows=1, clean_rows=1, failures=[])
        row = _make_handoff_row()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview.jsonl"
            cfg.export.openkb_handoff.preview_jsonl_path = str(path)

            with (
                patch(
                    "zotero_docai_pipeline.cli.commands.build_openkb_handoff_rows",
                    return_value=[row],
                ),
                patch(
                    "zotero_docai_pipeline.cli.commands.validate_openkb_handoff_rows",
                    return_value=report,
                ),
            ):
                exit_code = dry_run_command(cfg, logger, mock_zotero)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertNotEqual(payload, row.to_dict())
        self.assertEqual(
            payload["schema_version"],
            "openkb-docai-handoff-preview/v0.1",
        )
        self.assertFalse(payload["authoritative"])


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
