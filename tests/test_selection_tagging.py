"""Unit tests for selection tagging planning, application, dry-run preview, and validation."""

import unittest
from unittest.mock import MagicMock, patch

from zotero_docai_pipeline.cli.commands import (
    _determine_exit_code,
    dry_run_command,
    process_command,
)
from zotero_docai_pipeline.cli.main import validate_flags
from zotero_docai_pipeline.clients.exceptions import ZoteroClientError
from zotero_docai_pipeline.domain.config import (
    AppConfig,
    AttachmentUrlExportConfig,
    AuthQueryConfig,
    ConfigError,
    DownloadConfig,
    ExportConfig,
    MistralOCRConfig,
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
    DiscoveredItem,
    DiscoveryStats,
    PaperMetadata,
)
from zotero_docai_pipeline.orchestration.pipeline import Pipeline, ProcessingTagResult


def _make_app_config(**overrides):
    """Build a minimal AppConfig with selection tagging enabled by default."""
    selection_tagging = overrides.pop(
        "selection_tagging",
        SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=["sel-tag"]),
            remove=TagTargetConfig(values=[]),
        ),
    )
    tag_adding = overrides.pop("tag_adding", TagAddingConfig(enabled=False))
    processing = overrides.pop("processing", ProcessingConfig(dry_run=False))
    credentials = overrides.pop(
        "credentials",
        AuthQueryConfig(
            library_id="123",
            read_key="read-key",
            write_key="write-key",
        ),
    )
    return AppConfig(
        zotero=ZoteroConfig(),
        ocr=overrides.pop("ocr", MistralOCRConfig(enabled=False)),
        processing=processing,
        storage=StorageConfig(),
        credentials=credentials,
        tagging=overrides.pop(
            "tagging",
            TaggingConfig(
                selection=TagSelectionConfig(
                    include=TagRuleConfig(values=["docai"])
                )
            ),
        ),
        tag_adding=tag_adding,
        selection_tagging=selection_tagging,
        **overrides,
    )


def _make_pipeline(selection_tagging_config):
    """Build a Pipeline without __init__, with mocked client and logger."""
    pipeline = object.__new__(Pipeline)
    pipeline.selection_tagging_config = selection_tagging_config
    pipeline.zotero_client = MagicMock()
    pipeline.logger = MagicMock()
    return pipeline, pipeline.zotero_client


def _make_item(key, title, tags=None, citation_key=None):
    """Return a DiscoveredItem with minimal fields."""
    return DiscoveredItem(
        key=key,
        title=title,
        tags=tags or [],
        attachments=[],
        citation_key=citation_key,
        paper_metadata=PaperMetadata(),
    )


def _make_discovery_stats():
    """Return minimal DiscoveryStats."""
    return DiscoveryStats(matched_count=1, excluded_count=0, excluded_by_rule={})


class TestStandaloneAddOnly(unittest.TestCase):
    """Tests for _apply_selection_tagging with add-only configuration."""

    def test_add_only_calls_add_tag_per_item(self):
        config = SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=["tag-a", "tag-b"]),
            remove=TagTargetConfig(values=[]),
        )
        pipeline, mock_client = _make_pipeline(config)
        mock_client.add_tag.return_value = None
        items = [_make_item("ITEM1", "One"), _make_item("ITEM2", "Two")]

        agg, item_succeeded, item_failed = pipeline._apply_selection_tagging(items)

        self.assertEqual(item_succeeded, 2)
        self.assertEqual(item_failed, 0)
        self.assertEqual(agg.add_attempted, 4)
        self.assertEqual(agg.add_succeeded, 4)
        self.assertEqual(mock_client.add_tag.call_count, 4)
        mock_client.remove_tag.assert_not_called()


class TestStandaloneRemoveOnly(unittest.TestCase):
    """Tests for _apply_selection_tagging with remove-only configuration."""

    def test_remove_only_calls_remove_tag_per_item(self):
        config = SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=[]),
            remove=TagTargetConfig(values=["old-tag"]),
        )
        pipeline, mock_client = _make_pipeline(config)
        mock_client.remove_tag.return_value = None
        items = [_make_item("ITEM1", "One")]

        agg, item_succeeded, item_failed = pipeline._apply_selection_tagging(items)

        self.assertEqual(item_succeeded, 1)
        self.assertEqual(item_failed, 0)
        self.assertEqual(agg.remove_attempted, 1)
        self.assertEqual(agg.remove_succeeded, 1)
        mock_client.remove_tag.assert_called_once_with("ITEM1", "old-tag")
        mock_client.add_tag.assert_not_called()


class TestAddAndRemoveCombined(unittest.TestCase):
    """Tests for _apply_selection_tagging with both add and remove lists."""

    def test_combined_add_and_remove_per_item(self):
        config = SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=["new"]),
            remove=TagTargetConfig(values=["old"]),
        )
        pipeline, mock_client = _make_pipeline(config)
        mock_client.add_tag.return_value = None
        mock_client.remove_tag.return_value = None
        items = [_make_item("ITEM1", "One"), _make_item("ITEM2", "Two")]

        agg, item_succeeded, item_failed = pipeline._apply_selection_tagging(items)

        self.assertEqual(item_succeeded, 2)
        self.assertEqual(item_failed, 0)
        self.assertEqual(agg.add_attempted, 2)
        self.assertEqual(agg.remove_attempted, 2)
        self.assertEqual(mock_client.add_tag.call_count, 2)
        self.assertEqual(mock_client.remove_tag.call_count, 2)


class TestDryRunPreview(unittest.TestCase):
    """Tests for dry-run selection tagging preview without writes."""

    @patch("zotero_docai_pipeline.cli.commands.build_export_records")
    def test_dry_run_preview_without_tag_writes(self, mock_build_export):
        mock_build_export.return_value = []
        cfg = _make_app_config(
            processing=ProcessingConfig(dry_run=True),
            selection_tagging=SelectionTaggingConfig(
                enabled=True,
                add=TagTargetConfig(values=["add-me"]),
                remove=TagTargetConfig(values=["remove-me"]),
            ),
            export=ExportConfig(
                attachment_urls=AttachmentUrlExportConfig(enabled=True, log=True)
            ),
        )
        item = _make_item("ITEM1", "Test Paper", tags=["existing"])
        discovery_stats = _make_discovery_stats()
        mock_zotero_client = MagicMock()
        mock_zotero_client.get_items_by_selection.return_value = (
            [item],
            discovery_stats,
        )
        logger = MagicMock()

        dry_run_command(cfg, logger, mock_zotero_client)

        mock_zotero_client.add_tag.assert_not_called()
        mock_zotero_client.remove_tag.assert_not_called()
        mock_build_export.assert_called_once()

        info_messages = [
            str(c.args[0]) for c in logger.info.call_args_list if c.args
        ]
        joined = " ".join(info_messages)
        self.assertNotIn("On success", joined)
        self.assertNotIn("On failure", joined)
        self.assertIn("Would add", joined)
        self.assertIn("Would remove", joined)
        self.assertIn("Selected items", joined)
        self.assertIn("Planned add ops", joined)
        self.assertIn("Planned remove ops", joined)
        self.assertIn("Selection Tagging Preview", joined)
        self.assertTrue(
            any("nothing to export" in msg.lower() for msg in info_messages),
            "export preview should coexist with selection tagging preview",
        )
        st_idx = next(
            i for i, msg in enumerate(info_messages) if "Selection Tagging Preview" in msg
        )
        export_idx = next(
            i
            for i, msg in enumerate(info_messages)
            if "nothing to export" in msg.lower()
        )
        self.assertLess(st_idx, export_idx)


class TestItemsWithoutCitationKey(unittest.TestCase):
    """Tests that selection tagging uses item.key when citation_key is absent."""

    def test_tags_applied_using_item_key(self):
        config = SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=["tag-a"]),
            remove=TagTargetConfig(values=[]),
        )
        pipeline, mock_client = _make_pipeline(config)
        mock_client.add_tag.return_value = None
        item = _make_item("ITEM-KEY", "No Citation Key", citation_key=None)

        pipeline._apply_selection_tagging([item])

        mock_client.add_tag.assert_called_once_with("ITEM-KEY", "tag-a")


class TestErrorIsolation(unittest.TestCase):
    """Tests that one item failure does not stop processing of others."""

    def test_add_failure_on_one_item_isolates_remaining_items(self):
        config = SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=["tag-a"]),
            remove=TagTargetConfig(values=[]),
        )
        pipeline, mock_client = _make_pipeline(config)
        item1 = _make_item("ITEM1", "One")
        item2 = _make_item("ITEM2", "Two")

        def add_tag_side_effect(item_key, tag):
            if item_key == "ITEM1":
                raise ZoteroClientError("API error")
            return None

        mock_client.add_tag.side_effect = add_tag_side_effect

        agg, item_succeeded, item_failed = pipeline._apply_selection_tagging(
            [item1, item2]
        )

        self.assertEqual(item_failed, 1)
        self.assertEqual(item_succeeded, 1)
        self.assertEqual(agg.add_failed, 1)
        self.assertEqual(agg.add_succeeded, 1)


class TestAddRemoveConflictConfigError(unittest.TestCase):
    """Tests for SelectionTaggingConfig add/remove overlap validation."""

    def test_overlapping_add_and_remove_raises_config_error(self):
        with self.assertRaises(ConfigError) as ctx:
            SelectionTaggingConfig(
                add=TagTargetConfig(values=["x"]),
                remove=TagTargetConfig(values=["x"]),
            )
        self.assertIn("conflicting tags", str(ctx.exception))


class TestSelectionTaggingWithTagAddingConfigError(unittest.TestCase):
    """Tests for validate_flags mutual exclusion of selection tagging and tag adding."""

    def test_both_enabled_raises_config_error(self):
        cfg = _make_app_config(
            selection_tagging=SelectionTaggingConfig(
                enabled=True,
                add=TagTargetConfig(values=["a"]),
            ),
            tag_adding=TagAddingConfig(enabled=True, assignments={"key": ["b"]}),
        )
        with self.assertRaises(ConfigError):
            validate_flags(cfg)


class TestAlreadyPresentAddAndAbsentRemoveAreNoOps(unittest.TestCase):
    """Tests for no-op add/remove API responses still counting as success."""

    def test_no_op_responses_yield_success_counts(self):
        config = SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=["present"]),
            remove=TagTargetConfig(values=["absent"]),
        )
        pipeline, mock_client = _make_pipeline(config)
        mock_client.add_tag.return_value = None
        mock_client.remove_tag.return_value = None
        item = _make_item("ITEM1", "One", tags=["present"])

        agg, item_succeeded, item_failed = pipeline._apply_selection_tagging([item])

        self.assertEqual(item_succeeded, 1)
        self.assertEqual(item_failed, 0)
        self.assertEqual(agg.add_failed, 0)
        self.assertEqual(agg.remove_failed, 0)


class TestLiveSelectionTaggingExportLogOrder(unittest.TestCase):
    """Tests visible log order for live selection-tagging plus export runs."""

    @patch("zotero_docai_pipeline.cli.commands.Pipeline")
    @patch("zotero_docai_pipeline.cli.commands.ItemProcessor")
    def test_selection_tagging_summary_before_export_logs(
        self, mock_processor_cls, mock_pipeline_cls
    ):
        logger = MagicMock()
        cfg = _make_app_config(
            selection_tagging=SelectionTaggingConfig(
                enabled=True,
                add=TagTargetConfig(values=["sel-add"]),
                remove=TagTargetConfig(values=[]),
            ),
            export=ExportConfig(
                attachment_urls=AttachmentUrlExportConfig(enabled=True, log=True)
            ),
            ocr=MistralOCRConfig(enabled=False),
            download=DownloadConfig(enabled=False),
            tag_adding=TagAddingConfig(enabled=False),
        )
        mock_pipeline_cls.return_value.run.return_value = {
            "selection_tagging_selected": 1,
            "selection_tagging_item_succeeded": 1,
            "selection_tagging_item_failed": 0,
            "selection_tagging_add_succeeded": 1,
            "selection_tagging_add_failed": 0,
            "selection_tagging_remove_succeeded": 0,
            "selection_tagging_remove_failed": 0,
            "selection_tagging_summary_displayed": True,
            "total_time": 0.0,
        }
        mock_zotero_client = MagicMock()

        process_command(cfg, logger, mock_zotero_client, None)

        info_messages = [
            str(c.args[0]) for c in logger.info.call_args_list if c.args
        ]
        summary_count = sum(
            1 for msg in info_messages if "Selection Tagging Summary" in msg
        )
        self.assertEqual(
            summary_count,
            0,
            "pipeline already emitted summary; process_command must not duplicate",
        )

    def test_pipeline_logs_summary_before_export_in_standalone_mode(self):
        pipeline = object.__new__(Pipeline)
        pipeline.logger = MagicMock()
        pipeline.processing_config = ProcessingConfig()
        pipeline.tree_processor = None
        pipeline.tree_structure_config = TreeStructureConfig()
        pipeline.download_config = DownloadConfig(enabled=False)
        pipeline.tag_adding_config = TagAddingConfig(enabled=False)
        pipeline.selection_tagging_config = SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=["sel-add"]),
            remove=TagTargetConfig(values=[]),
        )
        pipeline.ocr_config = MistralOCRConfig(enabled=False)
        pipeline.export_config = ExportConfig(
            attachment_urls=AttachmentUrlExportConfig(enabled=True, log=True)
        )
        pipeline.zotero_client = MagicMock()

        item = _make_item("ITEM1", "Paper")
        discovery_stats = _make_discovery_stats()
        tag_result = ProcessingTagResult(
            outcome="selection_tagging",
            add_attempted=1,
            add_succeeded=1,
            add_failed=0,
            remove_attempted=0,
            remove_succeeded=0,
            remove_failed=0,
        )

        with patch.object(
            pipeline, "_discover_items", return_value=([item], discovery_stats)
        ):
            with patch.object(
                pipeline,
                "_apply_selection_tagging",
                return_value=(tag_result, 1, 0),
            ):
                with patch(
                    "zotero_docai_pipeline.orchestration.pipeline.build_export_records",
                    return_value=[],
                ):
                    summary = pipeline.run()

        info_messages = [
            str(c.args[0]) for c in pipeline.logger.info.call_args_list if c.args
        ]
        summary_idx = next(
            i for i, msg in enumerate(info_messages) if "Selection Tagging Summary" in msg
        )
        export_idx = next(
            i
            for i, msg in enumerate(info_messages)
            if "nothing to export" in msg.lower()
        )
        self.assertLess(summary_idx, export_idx)
        self.assertTrue(summary.get("selection_tagging_summary_displayed"))


class TestLiveExportRunsAfterSelectionTagging(unittest.TestCase):
    """Tests that export runs after selection tagging in standalone mode."""

    def test_export_runs_after_selection_tagging_in_standalone_mode(self):
        pipeline = object.__new__(Pipeline)
        pipeline.logger = MagicMock()
        pipeline.processing_config = ProcessingConfig()
        pipeline.tree_processor = None
        pipeline.tree_structure_config = TreeStructureConfig()
        pipeline.download_config = DownloadConfig(enabled=False)
        pipeline.tag_adding_config = TagAddingConfig(enabled=False)
        pipeline.selection_tagging_config = SelectionTaggingConfig(
            enabled=True,
            add=TagTargetConfig(values=["sel-add"]),
            remove=TagTargetConfig(values=[]),
        )
        pipeline.ocr_config = MistralOCRConfig(enabled=False)
        pipeline.export_config = ExportConfig(
            attachment_urls=AttachmentUrlExportConfig(enabled=True)
        )
        pipeline.zotero_client = MagicMock()

        item = _make_item("ITEM1", "Paper")
        discovery_stats = _make_discovery_stats()
        tag_result = ProcessingTagResult(
            outcome="selection_tagging",
            add_attempted=1,
            add_succeeded=1,
            add_failed=0,
            remove_attempted=0,
            remove_succeeded=0,
            remove_failed=0,
        )
        call_order: list[str] = []

        def track_apply(items):
            call_order.append("selection_tagging")
            return (tag_result, len(items), 0)

        def track_export(*args, **kwargs):
            call_order.append("export")
            return []

        with patch.object(
            pipeline, "_discover_items", return_value=([item], discovery_stats)
        ):
            with patch.object(
                pipeline, "_apply_selection_tagging", side_effect=track_apply
            ):
                with patch(
                    "zotero_docai_pipeline.orchestration.pipeline.build_export_records",
                    side_effect=track_export,
                ):
                    pipeline.run()

        self.assertEqual(call_order, ["selection_tagging", "export"])


class TestDetermineExitCode(unittest.TestCase):
    """Tests for _determine_exit_code selection-tagging and mixed-feature outcomes."""

    def test_standalone_selection_tagging_success(self):
        summary = {
            "selection_tagging_selected": 2,
            "selection_tagging_item_succeeded": 2,
            "selection_tagging_item_failed": 0,
            "successful_items": 0,
            "failed_items": 0,
            "total_items": 0,
        }
        self.assertEqual(_determine_exit_code(summary), 0)

    def test_standalone_selection_tagging_partial_failure(self):
        summary = {
            "selection_tagging_selected": 2,
            "selection_tagging_item_succeeded": 1,
            "selection_tagging_item_failed": 1,
            "successful_items": 0,
            "failed_items": 0,
            "total_items": 0,
        }
        self.assertEqual(_determine_exit_code(summary), 1)

    def test_standalone_selection_tagging_complete_failure(self):
        summary = {
            "selection_tagging_selected": 2,
            "selection_tagging_item_succeeded": 0,
            "selection_tagging_item_failed": 2,
            "successful_items": 0,
            "failed_items": 0,
            "total_items": 0,
        }
        self.assertEqual(_determine_exit_code(summary), 2)

    def test_combined_run_worst_case_when_downstream_succeeds(self):
        """Full selection-tagging failure must return 2 even if OCR/download succeeded."""
        summary = {
            "selection_tagging_selected": 3,
            "selection_tagging_item_succeeded": 0,
            "selection_tagging_item_failed": 3,
            "successful_items": 3,
            "failed_items": 0,
            "total_items": 3,
        }
        self.assertEqual(_determine_exit_code(summary), 2)


class TestProcessCommandSummaryOrdering(unittest.TestCase):
    """Tests that live-run summaries emit selection tagging before downstream sections."""

    @patch("zotero_docai_pipeline.cli.commands.Pipeline")
    @patch("zotero_docai_pipeline.cli.commands.ItemProcessor")
    @patch("zotero_docai_pipeline.cli.commands._display_download_summary")
    @patch("zotero_docai_pipeline.cli.commands._display_selection_tagging_summary")
    def test_selection_tagging_summary_before_download_summary(
        self,
        mock_display_selection,
        mock_display_download,
        mock_processor_cls,
        mock_pipeline_cls,
    ):
        call_order: list[str] = []

        def track_selection(logger, summary):
            call_order.append("selection_tagging")

        def track_download(logger, summary):
            call_order.append("download")

        mock_display_selection.side_effect = track_selection
        mock_display_download.side_effect = track_download
        mock_pipeline_cls.return_value.run.return_value = {
            "selection_tagging_selected": 1,
            "selection_tagging_item_succeeded": 1,
            "selection_tagging_item_failed": 0,
            "selection_tagging_summary_displayed": False,
            "total_pdfs_downloaded": 1,
            "total_pdfs_failed": 0,
            "total_time": 0.0,
        }

        cfg = _make_app_config(
            selection_tagging=SelectionTaggingConfig(
                enabled=True,
                add=TagTargetConfig(values=["sel-tag"]),
                remove=TagTargetConfig(values=[]),
            ),
            download=DownloadConfig(enabled=True),
            ocr=MistralOCRConfig(enabled=False),
            tag_adding=TagAddingConfig(enabled=False),
        )
        logger = MagicMock()
        mock_zotero_client = MagicMock()

        exit_code = process_command(cfg, logger, mock_zotero_client, None)

        self.assertEqual(exit_code, 0)
        mock_display_selection.assert_called_once()
        mock_display_download.assert_called_once()
        self.assertEqual(
            call_order, ["selection_tagging", "download"]
        )


if __name__ == "__main__":
    unittest.main()
