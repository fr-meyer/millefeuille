"""Tests for OpenKB handoff row identity building and validation."""

import unittest
from unittest.mock import MagicMock

from millefeuille.clients.exceptions import (
    AttachmentIdentityError,
    HandoffSecurityError,
)
from millefeuille.clients.zotero_client import ZoteroClient
from millefeuille.domain.config import OpenKBHandoffExportConfig
from millefeuille.domain.models import (
    AttachmentInfo,
    DiscoveredItem,
    OpenKBHandoffRow,
    PaperMetadata,
)
from millefeuille.utils.export import (
    build_openkb_handoff_rows,
    validate_openkb_handoff_rows,
)


def _make_attachment(
    key="ATT1",
    filename="Smith - 2024 - Title.pdf",
    content_type="application/pdf",
    link_mode="imported_file",
    md5=None,
    sha256=None,
    file_size_bytes=None,
    zotero_version=None,
    item_type=None,
):
    return AttachmentInfo(
        key=key,
        filename=filename,
        content_type=content_type,
        link_mode=link_mode,
        md5=md5,
        sha256=sha256,
        file_size_bytes=file_size_bytes,
        zotero_version=zotero_version,
        item_type=item_type,
    )


def _make_item(
    key="ITEM1",
    title="A Paper",
    attachments=None,
    citation_key=None,
):
    return DiscoveredItem(
        key=key,
        title=title,
        tags=[],
        attachments=attachments or [],
        citation_key=citation_key,
        paper_metadata=PaperMetadata(),
    )


def _make_zotero_client(library_id="123456"):
    client = object.__new__(ZoteroClient)
    client.credentials = MagicMock(library_id=library_id)
    client.download_pdf = MagicMock()
    return client


def _make_config(**overrides):
    return OpenKBHandoffExportConfig(**overrides)


def _build_rows(attachment, **item_kwargs):
    item = _make_item(attachments=[attachment], **item_kwargs)
    return build_openkb_handoff_rows(
        [item], _make_zotero_client(), _make_config()
    )


def _make_valid_row(**overrides):
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
    defaults = {
        "schema_version": "openkb-millefeuille-handoff/v0.1",
        "source_type": "zotero",
        "discovered_at": "2024-01-01T00:00:00+00:00",
        "item_key": "ITEM1",
        "attachment_key": "ATT1",
        "canonical_filename": "Smith - 2024 - Title.pdf",
        "is_pdf": True,
        "verification_strength": "hash-only",
        "recovery": recovery,
        "openkb_policy_hints": openkb_policy_hints,
    }
    defaults.update(overrides)
    return OpenKBHandoffRow(**defaults)


class TestBuildOpenkbHandoffRows(unittest.TestCase):
    def test_valid_attachment_produces_row(self):
        attachment = _make_attachment()
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0], OpenKBHandoffRow)
        self.assertTrue(rows[0].is_pdf)

    def test_non_pdf_attachment_skipped(self):
        attachment = _make_attachment(
            filename="photo.png", content_type="image/png"
        )
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        self.assertEqual(rows, [])

    def test_verification_strength_hash_only(self):
        attachment = _make_attachment(md5="abc123")
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        self.assertEqual(rows[0].verification_strength, "hash-only")

    def test_verification_strength_metadata_only(self):
        attachment = _make_attachment(file_size_bytes=1024)
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        self.assertEqual(rows[0].verification_strength, "metadata-only")

    def test_verification_strength_key_only(self):
        attachment = _make_attachment()
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        self.assertEqual(rows[0].verification_strength, "key-only")

    def test_sha256_optional_in_v0_1(self):
        attachment = _make_attachment()
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        self.assertIsNone(rows[0].sha256)
        self.assertNotEqual(rows[0].verification_strength, "full")

    def test_computed_sha256_gives_full_verification_strength(self):
        attachment = _make_attachment(md5="abc123")
        item = _make_item(attachments=[attachment])
        client = _make_zotero_client()
        client.download_pdf.return_value = b"%PDF-1.7 example"

        rows = build_openkb_handoff_rows(
            [item],
            client,
            _make_config(compute_sha256=True),
        )

        self.assertEqual(
            rows[0].sha256,
            "7b90830743ea12df73f6631d1437ab6d80f6f972b642bb6dc6bfda3c203fcc0f",
        )
        self.assertEqual(rows[0].verification_strength, "full")
        client.download_pdf.assert_called_once_with("ITEM1", "ATT1")

    def test_existing_sha256_gives_full_without_download(self):
        attachment = _make_attachment(sha256="existing-sha")
        item = _make_item(attachments=[attachment])
        client = _make_zotero_client()

        rows = build_openkb_handoff_rows(
            [item],
            client,
            _make_config(compute_sha256=True),
        )

        self.assertEqual(rows[0].sha256, "existing-sha")
        self.assertEqual(rows[0].verification_strength, "full")
        client.download_pdf.assert_not_called()

    def test_no_auth_url_in_recovery(self):
        attachment = _make_attachment()
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        self.assertNotIn("url", rows[0].recovery)

    def test_openkb_policy_hints_present(self):
        attachment = _make_attachment()
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        self.assertTrue(rows[0].openkb_policy_hints["no_auth_url"])

    def test_recovery_dict_has_no_url_field(self):
        attachment = _make_attachment()
        item = _make_item(attachments=[attachment])
        rows = build_openkb_handoff_rows(
            [item], _make_zotero_client(), _make_config()
        )
        recovery = rows[0].recovery
        self.assertNotIn("url", recovery)
        self.assertEqual(
            set(recovery.keys()),
            {
                "method",
                "library_id",
                "library_type",
                "item_key",
                "attachment_key",
            },
        )


class TestValidateOpenkbHandoffRows(unittest.TestCase):
    def test_missing_attachment_key_reported(self):
        row = _make_valid_row(attachment_key="")
        report = validate_openkb_handoff_rows([row], mode="test")
        self.assertFalse(report.is_clean)
        self.assertTrue(
            any(isinstance(f, AttachmentIdentityError) for f in report.failures)
        )

    def test_linked_url_attachment_reported(self):
        row = _make_valid_row()
        linked = _make_attachment(key="ATT1", link_mode="linked_url")
        source = _make_item(attachments=[linked])
        report = validate_openkb_handoff_rows(
            [row], mode="test", source_items=[source]
        )
        self.assertTrue(
            any(isinstance(f, AttachmentIdentityError) for f in report.failures)
        )

    def test_generic_filename_reported(self):
        row = _make_valid_row(canonical_filename="file.pdf")
        report = validate_openkb_handoff_rows([row], mode="test")
        self.assertTrue(
            any(isinstance(f, AttachmentIdentityError) for f in report.failures)
        )

    def test_unknown_fallback_filename_reported(self):
        row = _make_valid_row(canonical_filename="unknown.pdf")
        report = validate_openkb_handoff_rows([row], mode="test")
        self.assertFalse(report.is_clean)
        self.assertTrue(
            any(isinstance(f, AttachmentIdentityError) for f in report.failures)
        )

    def test_duplicate_filename_same_item_reported(self):
        row1 = _make_valid_row(
            attachment_key="ATT1", canonical_filename="Smith - 2024 - Title.pdf"
        )
        row2 = _make_valid_row(
            attachment_key="ATT2", canonical_filename="Smith - 2024 - Title.pdf"
        )
        report = validate_openkb_handoff_rows([row1, row2], mode="test")
        self.assertTrue(
            any(isinstance(f, AttachmentIdentityError) for f in report.failures)
        )

    def test_valid_attachment_produces_clean_report(self):
        rows = _build_rows(_make_attachment())
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertTrue(report.is_clean)
        self.assertEqual(report.clean_rows, 1)

    def test_sha256_optional_does_not_require_full_strength(self):
        rows = _build_rows(_make_attachment(md5="abc123"))
        self.assertIsNone(rows[0].sha256)
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertTrue(report.is_clean)

    def test_md5_present_gives_hash_only(self):
        rows = _build_rows(_make_attachment(md5="abc"))
        self.assertEqual(rows[0].verification_strength, "hash-only")
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertTrue(report.is_clean)

    def test_file_size_no_hash_gives_metadata_only(self):
        rows = _build_rows(_make_attachment(file_size_bytes=1024))
        self.assertEqual(rows[0].verification_strength, "metadata-only")
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertTrue(report.is_clean)

    def test_no_size_no_hash_gives_key_only(self):
        rows = _build_rows(_make_attachment())
        self.assertEqual(rows[0].verification_strength, "key-only")
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertTrue(report.is_clean)

    def test_recovery_dict_has_no_url_field(self):
        rows = _build_rows(_make_attachment())
        self.assertNotIn("url", rows[0].recovery)
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertTrue(report.is_clean)

    def test_openkb_policy_hints_no_auth_url_true(self):
        rows = _build_rows(_make_attachment())
        self.assertTrue(rows[0].openkb_policy_hints["no_auth_url"])
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertTrue(report.is_clean)

    def test_security_failure_in_validation_path(self):
        recovery = {
            "method": "Bearer eyJhbGci...",
            "library_id": "123456",
            "library_type": "user",
            "item_key": "ITEM1",
            "attachment_key": "ATT1",
        }
        row = _make_valid_row(recovery=recovery)
        report = validate_openkb_handoff_rows([row], mode="test")
        self.assertTrue(
            any(isinstance(f, HandoffSecurityError) for f in report.failures)
        )


class TestWeakVerificationRows(unittest.TestCase):
    def test_key_only_appears_in_weak_rows(self):
        rows = _build_rows(_make_attachment())
        self.assertEqual(rows[0].verification_strength, "key-only")
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertEqual(len(report.weak_verification_rows), 1)
        self.assertEqual(
            report.weak_verification_rows[0]["verification_strength"],
            "key-only",
        )

    def test_metadata_only_appears_in_weak_rows(self):
        rows = _build_rows(_make_attachment(file_size_bytes=1024))
        self.assertEqual(rows[0].verification_strength, "metadata-only")
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertEqual(len(report.weak_verification_rows), 1)
        self.assertEqual(
            report.weak_verification_rows[0]["verification_strength"],
            "metadata-only",
        )

    def test_hash_only_appears_in_weak_rows(self):
        rows = _build_rows(_make_attachment(md5="abc123"))
        self.assertEqual(rows[0].verification_strength, "hash-only")
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertEqual(len(report.weak_verification_rows), 1)
        self.assertEqual(
            report.weak_verification_rows[0]["verification_strength"],
            "hash-only",
        )

    def test_full_strength_not_in_weak_rows(self):
        row = _make_valid_row(verification_strength="full")
        report = validate_openkb_handoff_rows([row], mode="test")
        self.assertEqual(report.weak_verification_rows, [])

    def test_weak_rows_do_not_cause_failure(self):
        rows = _build_rows(_make_attachment())
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertTrue(report.is_clean)
        self.assertTrue(report.weak_verification_rows)

    def test_failed_row_not_in_weak_rows(self):
        row = _make_valid_row(canonical_filename="file.pdf")
        report = validate_openkb_handoff_rows([row], mode="test")
        self.assertFalse(report.is_clean)
        self.assertEqual(report.weak_verification_rows, [])

    def test_weak_row_dict_has_expected_keys(self):
        rows = _build_rows(_make_attachment())
        report = validate_openkb_handoff_rows(rows, mode="test")
        self.assertEqual(len(report.weak_verification_rows), 1)
        self.assertEqual(
            set(report.weak_verification_rows[0].keys()),
            {
                "item_key",
                "attachment_key",
                "canonical_filename",
                "verification_strength",
            },
        )


if __name__ == "__main__":
    unittest.main()
