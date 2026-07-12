"""Tests for explicit Zotero read/write credential policy."""

import unittest

from millefeuille.cli.main import validate_flags
from millefeuille.domain.config import (
    AppConfig,
    AttachmentUrlExportConfig,
    AuthQueryConfig,
    ConfigError,
    ExportConfig,
    MistralOCRConfig,
    ProcessingConfig,
    SelectionTaggingConfig,
    StorageConfig,
    TaggingConfig,
    TagRuleConfig,
    TagSelectionConfig,
    TagTargetConfig,
    ZoteroConfig,
)


def _make_config(credentials: AuthQueryConfig, **overrides) -> AppConfig:
    """Build a minimal AppConfig for credential validation tests."""
    return AppConfig(
        zotero=ZoteroConfig(),
        ocr=MistralOCRConfig(enabled=False),
        processing=overrides.pop("processing", ProcessingConfig(dry_run=False)),
        storage=StorageConfig(),
        credentials=credentials,
        tagging=TaggingConfig(
            selection=TagSelectionConfig(
                include=TagRuleConfig(values=["millefeuille"])
            )
        ),
        **overrides,
    )


class TestZoteroCredentialsPolicy(unittest.TestCase):
    """Validate explicit read/write key semantics and concise errors."""

    def test_missing_read_key_fails_even_when_write_key_is_set(self):
        with self.assertRaises(ConfigError) as ctx:
            AuthQueryConfig(
                library_id="123",
                read_key="",
                write_key="write-key",
            )

        message = str(ctx.exception)
        self.assertIn("ZOTERO_READ_KEY is required", message)
        self.assertIn("read access", message)

    def test_validate_flags_keeps_missing_read_key_message_concise(self):
        credentials = AuthQueryConfig(
            library_id="123",
            read_key="read-key",
            write_key="write-key",
        )
        credentials.read_key = ""
        cfg = _make_config(
            credentials,
            export=ExportConfig(
                attachment_urls=AttachmentUrlExportConfig(enabled=True)
            ),
            processing=ProcessingConfig(dry_run=True),
        )

        with self.assertRaises(ConfigError) as ctx:
            validate_flags(cfg)

        message = str(ctx.exception)
        self.assertEqual(
            message,
            "ZOTERO_READ_KEY is required for Zotero read operations. "
            "Set it to a Zotero API key with read access.",
        )

    def test_same_key_value_for_read_and_write_is_accepted_explicitly(self):
        cfg = _make_config(
            AuthQueryConfig(
                library_id="123",
                read_key="same-write-capable-key",
                write_key="same-write-capable-key",
            ),
            selection_tagging=SelectionTaggingConfig(
                enabled=True,
                add=TagTargetConfig(values=["millefeuille-processed"]),
            ),
        )

        validate_flags(cfg)

    def test_missing_write_key_is_allowed_without_write_features(self):
        cfg = _make_config(
            AuthQueryConfig(
                library_id="123",
                read_key="read-key",
                write_key=None,
            ),
            export=ExportConfig(
                attachment_urls=AttachmentUrlExportConfig(enabled=True)
            ),
            processing=ProcessingConfig(dry_run=True),
        )

        validate_flags(cfg)

    def test_missing_write_key_error_is_write_specific(self):
        cfg = _make_config(
            AuthQueryConfig(
                library_id="123",
                read_key="read-key",
                write_key=None,
            ),
            selection_tagging=SelectionTaggingConfig(
                enabled=True,
                add=TagTargetConfig(values=["millefeuille-processed"]),
            ),
        )

        with self.assertRaises(ConfigError) as ctx:
            validate_flags(cfg)

        message = str(ctx.exception)
        self.assertIn(
            "ZOTERO_WRITE_KEY is required for Zotero write operations",
            message,
        )
        self.assertIn("selection_tagging.enabled", message)
        self.assertIn("write access", message)


if __name__ == "__main__":
    unittest.main()
