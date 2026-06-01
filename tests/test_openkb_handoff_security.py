"""Tests for OpenKB handoff row security sanitization."""

import unittest

from zotero_docai_pipeline.clients.exceptions import HandoffSecurityError
from zotero_docai_pipeline.utils.export import sanitize_handoff_row


def _row(**overrides):
    base = {
        "schema_version": "openkb-docai-handoff/v0.1",
        "source_type": "zotero",
        "discovered_at": "2024-01-01T00:00:00+00:00",
        "item_key": "ITEM1",
        "item_title": "A Paper",
        "attachment_key": "ATT1",
        "content_type": "application/pdf",
        "canonical_filename": "Smith - 2024 - Title.pdf",
        "is_pdf": True,
        "verification_strength": "key-only",
        "recovery": {
            "method": "zotero_api_attachment",
            "library_id": "123456",
            "library_type": "user",
            "item_key": "ITEM1",
            "attachment_key": "ATT1",
        },
        "openkb_policy_hints": {
            "no_auth_url": True,
            "prefer_canonical_filename": True,
            "verification_required": True,
            "source_type": "zotero",
        },
    }
    base.update(overrides)
    return base


class TestSanitizeHandoffRow(unittest.TestCase):
    def test_signed_url_key_param(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(
                _row(zotero_file_url="https://example.com/file?key=abc123")
            )

    def test_signed_url_token_param(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(
                _row(zotero_file_url="https://example.com/file?token=xyz")
            )

    def test_signed_url_access_token_param(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(
                _row(
                    zotero_file_url="https://example.com/file?access_token=abc"
                )
            )

    def test_signed_url_expires_param(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(
                _row(
                    zotero_file_url="https://example.com/file?expires=1234567890"
                )
            )

    def test_signed_url_x_amz_param(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(
                _row(
                    zotero_file_url=(
                        "https://s3.amazonaws.com/bucket/file"
                        "?X-Amz-Signature=abc"
                    )
                )
            )

    def test_file_url_with_query_string(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(
                _row(
                    zotero_file_url=(
                        "https://example.com/users/1/items/ABC/file?key=abc"
                    )
                )
            )

    def test_authorization_header_value(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(_row(note="Authorization: Bearer abc"))

    def test_bearer_token_value(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(_row(note="Bearer eyJhbGci..."))

    def test_basic_auth_value(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(_row(note="Basic dXNlcjpwYXNz"))

    def test_cookie_value(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(_row(note="Cookie: session=abc"))

    def test_pdf_base64_data_uri(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(
                _row(note="data:application/pdf;base64,JVBERi...")
            )

    def test_large_base64_blob(self):
        blob = "A" * 600
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(_row(note=blob))

    def test_pdf_magic_bytes(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(_row(note="%PDF-1.4 some content"))

    def test_nested_field_checked(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(
                _row(recovery={"method": "Bearer eyJhbGci..."})
            )

    def test_relative_url_with_sensitive_param(self):
        with self.assertRaises(HandoffSecurityError):
            sanitize_handoff_row(_row(note="/api/items?token=secret"))

    def test_clean_row_passes(self):
        row = _row()
        result = sanitize_handoff_row(row)
        self.assertIs(result, row)


if __name__ == "__main__":
    unittest.main()
