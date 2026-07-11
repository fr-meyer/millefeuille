"""Offline coverage for multi-attachment and non-PDF OpenKB handoff evidence."""

import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from zotero_docai_pipeline.domain.config import OpenKBHandoffExportConfig
from zotero_docai_pipeline.domain.models import (
    AttachmentInfo,
    DiscoveredItem,
    OpenKBHandoffRow,
    PaperMetadata,
)
from zotero_docai_pipeline.utils.export import (
    build_openkb_acceptance_summary,
    build_openkb_handoff_rows,
    sanitize_handoff_row,
    validate_openkb_handoff_rows,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "openkb_handoff_multi_attachment"
)


def _load_jsonl(name: str) -> list[dict]:
    path = FIXTURE_DIR / name
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_item(attachments: list[AttachmentInfo]) -> DiscoveredItem:
    return DiscoveredItem(
        key="MULTIITEM1",
        title="Redacted Multi-Attachment Fixture Paper",
        tags=["docai-test"],
        attachments=attachments,
        citation_key="redactedMultiAttachment2026",
        paper_metadata=PaperMetadata(),
    )


class TestOpenkbHandoffMultiAttachmentFixture(unittest.TestCase):
    def test_fixture_rows_are_clean_and_joined_per_attachment(self):
        handoff_rows = _load_jsonl("handoff.live.jsonl")
        outcomes = _load_jsonl("openkb_outcomes.jsonl")
        duplicate_scans = _load_jsonl("duplicate_scans.jsonl")
        typed_rows = [OpenKBHandoffRow(**row) for row in handoff_rows]

        report = validate_openkb_handoff_rows(typed_rows, mode="fixture")
        summary = build_openkb_acceptance_summary(
            typed_rows,
            outcomes,
            duplicate_scans,
        )

        self.assertTrue(report.is_clean, report.failures)
        self.assertEqual(report.total_rows, 2)
        self.assertEqual({row["item_key"] for row in handoff_rows}, {"MULTIITEM1"})
        self.assertEqual(
            {row["attachment_key"] for row in handoff_rows},
            {"MULTIPDF1", "MULTIPDF2"},
        )
        self.assertEqual(
            {row["canonical_filename"] for row in handoff_rows},
            {
                "Redacted Multi - 2026 - Main paper.pdf",
                "Redacted Multi - 2026 - Supplement.pdf",
            },
        )
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(summary["counts"]["handoff_rows"], 2)
        self.assertEqual(summary["counts"]["openkb_added"], 2)
        self.assertEqual(summary["counts"]["duplicate_scans"], 2)
        self.assertEqual(summary["counts"]["joined_imports"], 2)
        self.assertEqual(summary["counts"]["skipped_total"], 1)
        self.assertEqual(
            summary["counts"]["skipped_by_event"],
            {"skipped-non-pdf-attachment": 1},
        )
        self.assertEqual(
            {entry["handoff"]["attachment_key"] for entry in summary["imports"]},
            {"MULTIPDF1", "MULTIPDF2"},
        )
        self.assertEqual(summary["unmatched"]["imports"], [])
        self.assertEqual(summary["unmatched"]["handoff_rows"], [])
        self.assertEqual(summary["unmatched"]["duplicate_scans"], [])
        for record in [*handoff_rows, *outcomes, *duplicate_scans, summary]:
            sanitize_handoff_row(record)

    def test_builder_exports_only_pdf_attachments_from_mixed_item(self):
        pdf_one = AttachmentInfo(
            key="MULTIPDF1",
            filename="Redacted Multi - 2026 - Main paper.pdf",
            content_type="application/pdf",
            link_mode="imported_file",
            sha256="4" * 64,
        )
        non_pdf = AttachmentInfo(
            key="MULTINONPDF1",
            filename="redacted-supplemental-image.png",
            content_type="image/png",
            link_mode="imported_file",
        )
        pdf_two = AttachmentInfo(
            key="MULTIPDF2",
            filename="Redacted Multi - 2026 - Supplement.pdf",
            content_type="application/pdf",
            link_mode="imported_file",
            sha256="5" * 64,
        )
        client = MagicMock()
        client.credentials = MagicMock(library_id="00000000")

        rows = build_openkb_handoff_rows(
            [_make_item([pdf_one, non_pdf, pdf_two])],
            client,
            OpenKBHandoffExportConfig(),
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row.attachment_key for row in rows}, {"MULTIPDF1", "MULTIPDF2"}
        )
        self.assertNotIn("MULTINONPDF1", {row.attachment_key for row in rows})
        self.assertEqual({row.verification_strength for row in rows}, {"full"})
        client.download_pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
