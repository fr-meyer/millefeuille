"""Offline fixture coverage for OpenKB handoff source-version drift."""

import json
from pathlib import Path
import unittest

from millefeuille.clients.exceptions import AttachmentIdentityError
from millefeuille.domain.models import OpenKBHandoffRow
from millefeuille.utils.export import (
    sanitize_handoff_row,
    verify_openkb_handoff_recovered_bytes,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "openkb_source_version_drift"
    / "source-version-drift.json"
)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_handoff_row(fixture: dict) -> OpenKBHandoffRow:
    return OpenKBHandoffRow(**fixture["handoff_row"])


class TestOpenkbSourceVersionDriftFixture(unittest.TestCase):
    def test_matching_recovered_bytes_pass_hash_verification(self):
        fixture = _load_fixture()
        row = _load_handoff_row(fixture)
        recovered_bytes = fixture["matching_recovered_bytes_utf8"].encode()

        actual_sha256 = verify_openkb_handoff_recovered_bytes(
            row, recovered_bytes
        )

        self.assertEqual(actual_sha256, fixture["expected_matching_sha256"])

    def test_drifted_recovered_bytes_fail_hash_verification(self):
        fixture = _load_fixture()
        row = _load_handoff_row(fixture)
        recovered_bytes = fixture["drifted_recovered_bytes_utf8"].encode()

        with self.assertRaises(AttachmentIdentityError) as raised:
            verify_openkb_handoff_recovered_bytes(row, recovered_bytes)

        message = str(raised.exception)
        self.assertIn("recovered bytes do not match", message)
        self.assertIn(row.item_key, message)
        self.assertIn(row.attachment_key, message)
        self.assertIn(fixture["expected_matching_sha256"], message)
        self.assertIn(fixture["expected_drifted_sha256"], message)

    def test_missing_handoff_sha_is_not_verifiable(self):
        fixture = _load_fixture()
        row = _load_handoff_row(fixture)
        row.sha256 = None

        with self.assertRaises(AttachmentIdentityError) as raised:
            verify_openkb_handoff_recovered_bytes(row, b"fixture bytes")

        self.assertIn("sha256: required", str(raised.exception))

    def test_fixture_is_redacted_and_payload_free(self):
        fixture = _load_fixture()
        row = _load_handoff_row(fixture)
        sanitize_handoff_row(row.to_dict())

        payload = json.dumps(fixture, sort_keys=True)
        self.assertNotIn("%PDF-", payload)
        self.assertNotIn("data:application/pdf", payload)
        self.assertNotIn("?", payload)
        self.assertNotIn("Authorization:", payload)
        self.assertNotIn("Bearer ", payload)


if __name__ == "__main__":
    unittest.main()
