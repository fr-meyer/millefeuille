"""Unit tests for outcome-based tag removal planning, application, and validation."""

import unittest
from unittest.mock import MagicMock, call

from zotero_docai_pipeline.cli.commands import dry_run_command
from zotero_docai_pipeline.cli.main import validate_flags
from zotero_docai_pipeline.clients.exceptions import ZoteroClientError
from zotero_docai_pipeline.clients.zotero_client import ZoteroClient
from zotero_docai_pipeline.domain.config import (
    AppConfig,
    AttachmentUrlExportConfig,
    AuthQueryConfig,
    ConfigError,
    DownloadConfig,
    ExportConfig,
    MistralOCRConfig,
    ProcessingConfig,
    StorageConfig,
    TagAddingConfig,
    TaggingConfig,
    TagRuleConfig,
    TagSelectionConfig,
    TagTargetConfig,
    ZoteroConfig,
)
from zotero_docai_pipeline.domain.models import (
    DiscoveredItem,
    DiscoveryStats,
    PaperMetadata,
)
from zotero_docai_pipeline.orchestration.pipeline import Pipeline, _plan_outcome_tags


def _make_tagging_config(**kwargs) -> TaggingConfig:
    """Build a TaggingConfig with valid selection and optional tag-target overrides."""
    tag_fields = {
        "apply_on_success": TagTargetConfig(),
        "apply_on_error": TagTargetConfig(),
        "remove_on_success": TagTargetConfig(),
        "remove_on_error": TagTargetConfig(),
    }
    for key, value in kwargs.items():
        if isinstance(value, list):
            tag_fields[key] = TagTargetConfig(values=value)
        else:
            tag_fields[key] = value
    return TaggingConfig(
        selection=TagSelectionConfig(include=TagRuleConfig(values=["docai"])),
        **tag_fields,
    )


def _make_pipeline(tagging_config, zotero_config):
    """Build a Pipeline without __init__, with mocked client and logger."""
    pipeline = object.__new__(Pipeline)
    pipeline.tagging_config = tagging_config
    pipeline.zotero_config = zotero_config
    pipeline.zotero_client = MagicMock()
    pipeline.logger = MagicMock()
    return pipeline, pipeline.zotero_client


class TestPlanOutcomeTags(unittest.TestCase):
    """Tests for _plan_outcome_tags success and failure branches."""

    def test_success_plan_contains_expected_add_and_remove(self):
        tagging_cfg = _make_tagging_config(
            apply_on_success=["done"],
            remove_on_success=["pending"],
        )
        zotero_cfg = ZoteroConfig(error_tagging_enabled=True)

        plan = _plan_outcome_tags(tagging_cfg, zotero_cfg, "success")

        self.assertEqual(plan.outcome, "success")
        self.assertEqual(plan.tags_to_add, ["done"])
        self.assertEqual(plan.tags_to_remove, ["pending"])

    def test_failure_plan_honors_error_tagging_enabled_for_adds_not_removals(self):
        tagging_cfg = _make_tagging_config(
            apply_on_error=["error"],
            remove_on_error=["pending"],
        )
        zotero_cfg = ZoteroConfig(error_tagging_enabled=False)

        plan = _plan_outcome_tags(tagging_cfg, zotero_cfg, "failure")

        self.assertEqual(plan.tags_to_add, [])
        self.assertEqual(plan.tags_to_remove, ["pending"])


class TestTaggingConfigValidation(unittest.TestCase):
    """Tests for TaggingConfig same-outcome conflict validation."""

    def test_same_tag_in_apply_and_remove_for_success_raises_config_error(self):
        with self.assertRaises(ConfigError) as ctx:
            TaggingConfig(
                selection=TagSelectionConfig(
                    include=TagRuleConfig(values=["docai"])
                ),
                apply_on_success=TagTargetConfig(values=["docai"]),
                remove_on_success=TagTargetConfig(values=["docai"]),
            )
        self.assertIn("conflicting tags", str(ctx.exception))

    def test_same_tag_in_apply_and_remove_for_error_raises_config_error(self):
        with self.assertRaises(ConfigError) as ctx:
            TaggingConfig(
                selection=TagSelectionConfig(
                    include=TagRuleConfig(values=["docai"])
                ),
                apply_on_error=TagTargetConfig(values=["docai"]),
                remove_on_error=TagTargetConfig(values=["docai"]),
            )
        self.assertIn("conflicting tags", str(ctx.exception))

    def test_cross_outcome_overlap_is_allowed(self):
        cfg = TaggingConfig(
            selection=TagSelectionConfig(include=TagRuleConfig(values=["docai"])),
            apply_on_success=TagTargetConfig(values=["docai"]),
            remove_on_error=TagTargetConfig(values=["docai"]),
        )
        self.assertEqual(cfg.apply_on_success.values, ["docai"])
        self.assertEqual(cfg.remove_on_error.values, ["docai"])


class TestApplyProcessingTagsSuccess(unittest.TestCase):
    """Tests for _apply_processing_tags on the success path."""

    def test_success_path_executes_plan_and_returns_correct_counts(self):
        tagging_cfg = _make_tagging_config(
            apply_on_success=["done"],
            remove_on_success=["pending"],
        )
        zotero_cfg = ZoteroConfig(error_tagging_enabled=True)
        pipeline, mock_client = _make_pipeline(tagging_cfg, zotero_cfg)
        mock_client.add_tag.return_value = None
        mock_client.remove_tag.return_value = None

        result = pipeline._apply_processing_tags("ITEM1", success=True)

        self.assertEqual(result.outcome, "success")
        self.assertEqual(result.add_attempted, 1)
        self.assertEqual(result.add_succeeded, 1)
        self.assertEqual(result.add_failed, 0)
        self.assertEqual(result.remove_attempted, 1)
        self.assertEqual(result.remove_succeeded, 1)
        self.assertEqual(result.remove_failed, 0)
        self.assertEqual(
            mock_client.add_tag.call_args_list, [call("ITEM1", "done")]
        )
        self.assertEqual(
            mock_client.remove_tag.call_args_list, [call("ITEM1", "pending")]
        )


class TestApplyProcessingTagsFailurePath(unittest.TestCase):
    """Tests for _apply_processing_tags on the failure path."""

    def test_empty_remove_on_error_leaves_failure_removals_empty(self):
        tagging_cfg = _make_tagging_config(
            apply_on_error=["error"],
            remove_on_error=[],
        )
        zotero_cfg = ZoteroConfig(error_tagging_enabled=True)
        pipeline, mock_client = _make_pipeline(tagging_cfg, zotero_cfg)

        result = pipeline._apply_processing_tags("ITEM1", success=False)

        self.assertEqual(result.remove_attempted, 0)
        self.assertEqual(result.remove_succeeded, 0)
        mock_client.remove_tag.assert_not_called()

    def test_failure_removals_run_even_when_error_tagging_disabled(self):
        tagging_cfg = _make_tagging_config(
            apply_on_error=["error"],
            remove_on_error=["pending"],
        )
        zotero_cfg = ZoteroConfig(error_tagging_enabled=False)
        pipeline, mock_client = _make_pipeline(tagging_cfg, zotero_cfg)
        mock_client.remove_tag.return_value = None

        result = pipeline._apply_processing_tags("ITEM1", success=False)

        self.assertEqual(result.add_attempted, 0)
        self.assertEqual(result.remove_attempted, 1)
        self.assertEqual(result.remove_succeeded, 1)
        mock_client.add_tag.assert_not_called()
        mock_client.remove_tag.assert_called_once_with("ITEM1", "pending")


class TestMissingTagRemoval(unittest.TestCase):
    """Tests for per-tag error isolation during tag removal."""

    def test_already_absent_tag_removal_is_a_noop_not_a_failure(self):
        tagging_cfg = _make_tagging_config(remove_on_success=["pending"])
        zotero_cfg = ZoteroConfig(error_tagging_enabled=True)
        pipeline, mock_client = _make_pipeline(tagging_cfg, zotero_cfg)
        mock_client.remove_tag.return_value = None

        result = pipeline._apply_processing_tags("ITEM1", success=True)

        self.assertEqual(result.remove_attempted, 1)
        self.assertEqual(result.remove_succeeded, 1)
        self.assertEqual(result.remove_failed, 0)
        self.assertEqual(result.add_failed, 0)
        self.assertFalse(result.has_failures)
        mock_client.remove_tag.assert_called_once_with("ITEM1", "pending")


class TestPartialTagOperationFailure(unittest.TestCase):
    """Tests for continuing remaining tag operations after partial failure."""

    def test_partial_failure_continues_remaining_operations_and_captures_counts(self):
        tagging_cfg = _make_tagging_config(
            apply_on_success=["tag-a", "tag-b"],
            remove_on_success=["pending"],
        )
        zotero_cfg = ZoteroConfig(error_tagging_enabled=True)
        pipeline, mock_client = _make_pipeline(tagging_cfg, zotero_cfg)

        def add_tag_side_effect(item_key, tag):
            if tag == "tag-a":
                raise ZoteroClientError("add failed")
            return None

        mock_client.add_tag.side_effect = add_tag_side_effect
        mock_client.remove_tag.return_value = None

        result = pipeline._apply_processing_tags("ITEM1", success=True)

        self.assertEqual(result.add_attempted, 2)
        self.assertEqual(result.add_succeeded, 1)
        self.assertEqual(result.add_failed, 1)
        self.assertEqual(result.remove_attempted, 1)
        self.assertEqual(result.remove_succeeded, 1)
        self.assertEqual(result.remove_failed, 0)
        self.assertEqual(mock_client.add_tag.call_count, 2)
        mock_client.remove_tag.assert_called_once()


class TestDryRunTwoBranchPreview(unittest.TestCase):
    """Tests for dry-run preview of both success and failure tag plans."""

    def test_dry_run_shows_both_success_and_failure_plans_per_item_without_writes(
        self,
    ):
        tagging = _make_tagging_config(
            apply_on_success=["done"],
            remove_on_success=["pending"],
            apply_on_error=["error"],
            remove_on_error=["queue"],
        )
        cfg = AppConfig(
            zotero=ZoteroConfig(error_tagging_enabled=True),
            ocr=MistralOCRConfig(enabled=False),
            processing=ProcessingConfig(dry_run=True),
            storage=StorageConfig(),
            credentials=AuthQueryConfig(library_id="123", read_key="read-key"),
            tagging=tagging,
        )
        item = DiscoveredItem(
            key="ITEM1",
            title="Test Paper",
            tags=["docai"],
            attachments=[],
            citation_key="test2024",
            paper_metadata=PaperMetadata(
                doi="10.1234/test",
                author_string="Author One",
            ),
        )
        discovery_stats = DiscoveryStats(
            matched_count=1,
            excluded_count=0,
            excluded_by_rule={},
        )
        mock_zotero_client = MagicMock()
        mock_zotero_client.get_items_by_selection.return_value = (
            [item],
            discovery_stats,
        )
        logger = MagicMock()

        dry_run_command(cfg, logger, mock_zotero_client)

        mock_zotero_client.add_tag.assert_not_called()
        mock_zotero_client.remove_tag.assert_not_called()

        info_messages = " ".join(
            str(c.args[0]) for c in logger.info.call_args_list if c.args
        )
        self.assertIn("On success:", info_messages)
        self.assertIn("On failure:", info_messages)
        for tag in ("done", "pending", "error", "queue"):
            self.assertIn(tag, info_messages)


class TestWriteKeyValidation(unittest.TestCase):
    """Tests for ZOTERO_WRITE_KEY requirement when removal lists are non-empty."""

    def _make_live_run_config(self, **tagging_kwargs):
        return AppConfig(
            zotero=ZoteroConfig(error_tagging_enabled=True),
            ocr=MistralOCRConfig(enabled=False),
            processing=ProcessingConfig(dry_run=False),
            storage=StorageConfig(),
            credentials=AuthQueryConfig(
                library_id="123",
                read_key="read-key",
                write_key=None,
            ),
            download=DownloadConfig(enabled=True),
            tag_adding=TagAddingConfig(enabled=False),
            tagging=_make_tagging_config(**tagging_kwargs),
        )

    def test_non_empty_remove_on_success_requires_write_key_in_live_run(self):
        cfg = self._make_live_run_config(remove_on_success=["pending"])

        with self.assertRaises(ConfigError) as ctx:
            validate_flags(cfg)

        message = str(ctx.exception)
        self.assertIn("ZOTERO_WRITE_KEY", message)
        self.assertIn("remove_on_success", message)

    def test_non_empty_remove_on_error_requires_write_key_in_live_run(self):
        cfg = self._make_live_run_config(remove_on_error=["pending"])

        with self.assertRaises(ConfigError) as ctx:
            validate_flags(cfg)

        message = str(ctx.exception)
        self.assertIn("ZOTERO_WRITE_KEY", message)
        self.assertIn("remove_on_error", message)

    def test_removal_lists_do_not_require_write_key_in_dry_run(self):
        cfg = AppConfig(
            zotero=ZoteroConfig(error_tagging_enabled=True),
            ocr=MistralOCRConfig(enabled=False),
            processing=ProcessingConfig(dry_run=True),
            storage=StorageConfig(),
            credentials=AuthQueryConfig(
                library_id="123",
                read_key="read-key",
                write_key=None,
            ),
            tagging=_make_tagging_config(remove_on_success=["pending"]),
            export=ExportConfig(
                attachment_urls=AttachmentUrlExportConfig(enabled=True)
            ),
        )

        validate_flags(cfg)


def _make_zotero_client():
    """Build a ZoteroClient without __init__, with mocked read/write clients."""
    client = object.__new__(ZoteroClient)
    client._zotero_read = MagicMock()
    mock_write_client = MagicMock()
    client._require_write_client = MagicMock(return_value=mock_write_client)
    stub_config = MagicMock()
    stub_config.library_id = "0"
    client.config = stub_config
    client.credentials = stub_config
    return client, mock_write_client


class TestRemoveTagClientNoOp(unittest.TestCase):
    """Direct ZoteroClient tests for absent-tag no-op behavior in remove_tag()."""

    def test_remove_tag_absent_tag_does_not_call_update_item(self):
        client, mock_write_client = _make_zotero_client()
        mock_write_client.item.return_value = {
            "key": "ITEM1",
            "data": {"key": "ITEM1", "tags": []},
        }

        result = client.remove_tag("ITEM1", "pending")

        self.assertIsNone(result)
        mock_write_client.update_item.assert_not_called()


if __name__ == "__main__":
    unittest.main()
