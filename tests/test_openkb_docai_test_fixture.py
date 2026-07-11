"""Acceptance checks for the redacted docai-test OpenKB handoff fixture."""

import base64
import binascii
import json
from pathlib import Path
import re
import unittest

from zotero_docai_pipeline.domain.models import OpenKBHandoffRow
from zotero_docai_pipeline.utils.export import (
    build_openkb_acceptance_summary,
    build_openkb_handoff_preview_rows,
    sanitize_handoff_row,
    validate_openkb_handoff_rows,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "openkb_handoff_docai_test"
)
SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "key",
    "password",
    "secret",
    "token",
}
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"authorization:\s*(bearer|basic)\s+", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~-]+", re.IGNORECASE),
    re.compile(r"\bbasic\s+[A-Za-z0-9._~-]+", re.IGNORECASE),
    re.compile(r"cookie:\s*", re.IGNORECASE),
    re.compile(r"data:application/pdf;base64,", re.IGNORECASE),
    re.compile(r"%PDF-"),
)


def _load_jsonl(name: str) -> list[dict]:
    path = FIXTURE_DIR / name
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _walk_json(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key, nested
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _looks_like_large_base64_blob(value: str) -> bool:
    compact = value.strip()
    if len(compact) < 200 or not re.fullmatch(r"[A-Za-z0-9+/=\s]+", compact):
        return False
    try:
        base64.b64decode(compact, validate=True)
    except binascii.Error:
        return False
    return True


class TestDocaiTestOpenKBHandoffFixture(unittest.TestCase):
    def test_live_fixture_rows_are_full_verified_and_secret_safe(self):
        rows = _load_jsonl("handoff.live.jsonl")
        handoff_rows = [OpenKBHandoffRow(**row) for row in rows]

        report = validate_openkb_handoff_rows(handoff_rows, mode="fixture")

        self.assertTrue(report.is_clean, report.failures)
        self.assertEqual(report.total_rows, 3)
        self.assertEqual(report.weak_verification_rows, [])
        self.assertEqual(len({row["attachment_key"] for row in rows}), 3)
        for row in rows:
            sanitize_handoff_row(row)
            self.assertEqual(row["schema_version"], "openkb-docai-handoff/v0.1")
            self.assertEqual(row["source_type"], "zotero")
            self.assertEqual(row["verification_strength"], "full")
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(row["content_type"], "application/pdf")
            self.assertTrue(row["is_pdf"])
            self.assertEqual(row["recovery"]["method"], "zotero_api_attachment")
            self.assertEqual(row["recovery"]["item_key"], row["item_key"])
            self.assertEqual(
                row["recovery"]["attachment_key"], row["attachment_key"]
            )
            self.assertNotIn("url", row["recovery"])
            self.assertTrue(row["openkb_policy_hints"]["no_auth_url"])

        self._assert_no_secret_or_payload_leakage(rows)

    def test_preview_fixture_is_redacted_non_authoritative_and_reproducible(self):
        live_rows = _load_jsonl("handoff.live.jsonl")
        expected_preview = _load_jsonl("handoff.preview.jsonl")

        generated_preview = build_openkb_handoff_preview_rows(
            [OpenKBHandoffRow(**row) for row in live_rows]
        )

        self.assertEqual(generated_preview, expected_preview)
        for preview_row, live_row in zip(
            expected_preview, live_rows, strict=True
        ):
            sanitize_handoff_row(preview_row)
            self.assertEqual(
                preview_row["schema_version"],
                "openkb-docai-handoff-preview/v0.1",
            )
            self.assertTrue(preview_row["preview"])
            self.assertFalse(preview_row["authoritative"])
            self.assertNotIn("item_key", preview_row)
            self.assertNotIn("attachment_key", preview_row)
            self.assertNotIn("citation_key", preview_row)
            self.assertNotIn("item_title", preview_row)
            self.assertNotIn("md5", preview_row)
            self.assertNotIn("sha256", preview_row)
            self.assertNotEqual(
                preview_row["identity"]["item_key"], live_row["item_key"]
            )
            self.assertNotEqual(
                preview_row["identity"]["attachment_key"],
                live_row["attachment_key"],
            )
            self.assertTrue(preview_row["identity"]["item_key"].count("..."))
            self.assertTrue(preview_row["recovery"]["redacted"])
            self.assertEqual(
                preview_row["omitted_fields"],
                [
                    "item_key",
                    "attachment_key",
                    "citation_key",
                    "item_title",
                    "md5",
                    "sha256",
                    "recovery.library_id",
                    "recovery.item_key",
                    "recovery.attachment_key",
                ],
            )

        self._assert_no_secret_or_payload_leakage(expected_preview)

    def test_openkb_outcomes_map_three_imports_and_one_no_pdf_skip(self):
        live_rows = _load_jsonl("handoff.live.jsonl")
        outcomes = _load_jsonl("openkb_outcomes.jsonl")
        imports = [row for row in outcomes if row["event"] == "openkb-added"]
        skipped = [row for row in outcomes if row["event"] == "skipped-no-pdf"]

        self.assertEqual(len(imports), 3)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["citation_key"], "fixtureNoPdfSampleD2026")
        self.assertIn("no PDF attachment", skipped[0]["reason"])
        self.assertEqual(
            {row["canonical_filename"] for row in imports},
            {row["canonical_filename"] for row in live_rows},
        )

        for row in imports:
            self.assertEqual(row["source_type"], "zotero")
            self.assertEqual(row["selected_route"], "merged-dual")
            self.assertTrue(row["dual_extraction_complete"])
            self.assertEqual(row["verification_result"], "ok")
            self.assertEqual(row["extraction_routes"], ["mistral-ocr", "native"])
            self.assertTrue(
                row["source_pack"].startswith("fixture/openkb/source-packs/zotero/")
            )
            self.assertTrue(
                row["openkb_raw_doc"].startswith("fixture/openkb/kbs/docai-main/raw/")
            )
            self.assertTrue(row["openkb_raw_doc"].endswith(".md"))

        self._assert_no_secret_or_payload_leakage(outcomes)

    def test_acceptance_summary_joins_handoff_outcomes_and_duplicate_scans(self):
        live_rows = _load_jsonl("handoff.live.jsonl")
        outcomes = _load_jsonl("openkb_outcomes.jsonl")
        duplicate_scans = _load_jsonl("duplicate_scans.jsonl")

        summary = build_openkb_acceptance_summary(
            [OpenKBHandoffRow(**row) for row in live_rows],
            outcomes,
            duplicate_scans,
        )

        self.assertEqual(
            summary["schema_version"],
            "openkb-docai-acceptance-summary/v0.1",
        )
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(summary["counts"]["handoff_rows"], 3)
        self.assertEqual(summary["counts"]["openkb_added"], 3)
        self.assertEqual(summary["counts"]["skipped_total"], 1)
        self.assertEqual(
            summary["counts"]["skipped_by_event"], {"skipped-no-pdf": 1}
        )
        self.assertEqual(summary["counts"]["duplicate_scans"], 3)
        self.assertEqual(summary["counts"]["joined_imports"], 3)
        self.assertEqual(summary["counts"]["import_join_failures"], 0)
        self.assertEqual(summary["counts"]["unmatched_handoff_rows"], 0)
        self.assertEqual(summary["counts"]["unmatched_duplicate_scans"], 0)
        self.assertEqual(summary["counts"]["duplicate_scan_review_rows"], 0)
        self.assertEqual(len(summary["imports"]), 3)
        for entry in summary["imports"]:
            self.assertTrue(entry["handoff"]["matched"])
            self.assertEqual(entry["handoff"]["match_count"], 1)
            self.assertEqual(entry["handoff"]["verification_strength"], "full")
            self.assertTrue(entry["handoff"]["sha256_present"])
            self.assertTrue(entry["duplicate_scan"]["matched"])
            self.assertEqual(entry["duplicate_scan"]["match_count"], 1)
            self.assertEqual(
                entry["duplicate_scan"]["evidence"]["scan_result"],
                "unique",
            )
        self.assertEqual(
            summary["skips"][0]["citation_key"], "fixtureNoPdfSampleD2026"
        )
        self.assertEqual(
            summary["unmatched"],
            {"imports": [], "handoff_rows": [], "duplicate_scans": []},
        )

        self._assert_no_secret_or_payload_leakage([summary])

    def test_acceptance_summary_marks_incomplete_or_duplicate_evidence_for_review(self):
        live_rows = _load_jsonl("handoff.live.jsonl")
        typed_rows = [OpenKBHandoffRow(**row) for row in live_rows]
        outcomes = _load_jsonl("openkb_outcomes.jsonl")
        duplicate_scans = _load_jsonl("duplicate_scans.jsonl")

        missing_scan_summary = build_openkb_acceptance_summary(
            typed_rows,
            outcomes,
            duplicate_scans[:-1],
        )
        self.assertEqual(missing_scan_summary["status"], "needs-review")
        self.assertEqual(
            missing_scan_summary["counts"]["import_join_failures"], 1
        )
        self.assertEqual(
            missing_scan_summary["unmatched"]["imports"][0][
                "duplicate_scan_match_count"
            ],
            0,
        )

        duplicate_scans[0]["matched_existing"] = True
        duplicate_scans[0]["match_count"] = 1
        duplicate_found_summary = build_openkb_acceptance_summary(
            typed_rows,
            outcomes,
            duplicate_scans,
        )
        self.assertEqual(duplicate_found_summary["status"], "needs-review")
        self.assertEqual(
            duplicate_found_summary["counts"]["duplicate_scan_review_rows"],
            1,
        )

    def test_attachment_keys_are_stable_idempotency_keys(self):
        live_rows = _load_jsonl("handoff.live.jsonl")
        seen_attachment_keys: set[str] = set()
        first_pass_imported = []
        second_pass_skipped = []

        for row in live_rows:
            attachment_key = row["attachment_key"]
            if attachment_key not in seen_attachment_keys:
                seen_attachment_keys.add(attachment_key)
                first_pass_imported.append(row)

        for row in live_rows:
            if row["attachment_key"] in seen_attachment_keys:
                second_pass_skipped.append(row)

        self.assertEqual(len(first_pass_imported), 3)
        self.assertEqual(len(second_pass_skipped), 3)
        self.assertEqual(
            {row["attachment_key"] for row in first_pass_imported},
            {row["attachment_key"] for row in second_pass_skipped},
        )

    def _assert_no_secret_or_payload_leakage(self, records: list[dict]) -> None:
        for record in records:
            for key, value in _walk_json(record):
                lower_key = key.lower()
                if lower_key in SENSITIVE_FIELD_NAMES:
                    self.fail(f"unexpected sensitive field in fixture: {key}")
                if isinstance(value, str):
                    self.assertNotIn("?", value, f"query string in {key}: {value}")
                    for pattern in SENSITIVE_TEXT_PATTERNS:
                        self.assertIsNone(
                            pattern.search(value),
                            f"sensitive value in {key}: {value}",
                        )
                    self.assertFalse(
                        _looks_like_large_base64_blob(value),
                        f"large base64-looking blob in {key}",
                    )


if __name__ == "__main__":
    unittest.main()
