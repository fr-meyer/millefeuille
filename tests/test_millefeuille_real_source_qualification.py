"""Original valid PDFs through the opt-in product extraction seam."""

import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.source_qualification import (
    SourceQualificationError,
    compare_native_sources,
    qualify_native_source,
)
from tests.fixtures.remaining_source_fixture import GOLDENS, layout_pdf


def synthetic_pdf(pages, comment=b"", rotate=False):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for lines in pages:
        n = len(objects) + 1
        kids.append(f"{n} 0 R")
        rotation = " /Rotate 90" if rotate else ""
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
                f" /Resources << /Font << /F1 3 0 R >> >>"
                f" /Contents {n + 1} 0 R{rotation} >>"
            ).encode()
        )
        stream = (
            b"BT /F1 12 Tf\n"
            + b"".join(
                f"1 0 0 1 {x} {y} Tm ({label}) Tj\n".encode() for x, y, label in lines
            )
            + b"ET"
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    ).encode()
    result = b"%PDF-1.4\n%" + comment + b"\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(result)
    result += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return (
        result
        + (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )


class RealSourceQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def observe(self, raw):
        path = self.root / "source.pdf"
        path.write_bytes(raw)
        return qualify_native_source(
            path, expected_sha256=hashlib.sha256(raw).hexdigest()
        )

    def test_s01_native_exact_pages_text_numbers_order(self):
        raw = synthetic_pdf(
            [
                [(20, 700, "First 17.25"), (20, 680, "Second 42")],
                [(20, 700, "Page two 99")],
            ]
        )
        result = self.observe(raw)
        self.assertEqual(result.pages, ("First 17.25\nSecond 42", "Page two 99"))
        self.assertEqual(result.source_sha256, hashlib.sha256(raw).hexdigest())
        self.assertTrue(result.tool.startswith("PyPDF2/"))

    def test_s04_layout_subcases_require_review(self):
        for case, golden in GOLDENS.items():
            with self.subTest(case=case):
                raw = layout_pdf(case)
                self.assertIn(golden["required"], raw)
                self.assertEqual(golden["page"], 1)
                self.assertTrue(golden["facts"])
                self.assertTrue(golden["locator"])
                result = self.observe(raw)
                self.assertEqual(len(result.pages), 1)
                self.assertEqual(result.source_sha256, hashlib.sha256(raw).hexdigest())
                self.assertEqual(result.disposition, "needs-review")
                self.assertIn(
                    "reading order follows content streams, not visual layout",
                    result.limitations,
                )
                self.assertIn(
                    "no table, figure, glyph, equation or semantic fidelity guarantee",
                    result.limitations,
                )
                # Golden geometry/math is deliberately NOT inferred from text.
                # An extraction observation must never promote it to acceptance.

    def test_s06_duplicates(self):
        pages = [[(20, 700, "Same 17")]]
        a = self.observe(synthetic_pdf(pages))
        b = self.observe(synthetic_pdf(pages, comment=b"variant"))
        self.assertEqual(compare_native_sources(a, a), "exact-duplicate-needs-review")
        self.assertNotEqual(a.source_sha256, b.source_sha256)
        for left, right in ((a, b), (b, a)):
            self.assertEqual(
                compare_native_sources(left, right),
                "same-text-different-bytes-needs-review",
            )

    def test_s07_competing_content_and_hash_drift(self):
        a = self.observe(synthetic_pdf([[(20, 700, "Value 17")]]))
        b = self.observe(synthetic_pdf([[(20, 700, "Value 18")]]))
        self.assertEqual(compare_native_sources(a, b), "competing-content-needs-review")
        with self.assertRaisesRegex(SourceQualificationError, "source-hash-drift"):
            qualify_native_source(
                self.root / "source.pdf", expected_sha256=a.source_sha256
            )

    def test_s08_missing_source_no_outputs(self):
        with self.assertRaisesRegex(
            SourceQualificationError, "source-unreadable-or-unsafe"
        ):
            qualify_native_source(self.root / "missing.pdf", expected_sha256="0" * 64)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_s09_matching_hash_actual_decoder_failure(self):
        with self.assertRaises(SourceQualificationError) as caught:
            self.observe(b"%PDF-1.4\nthis is a truncated PDF")
        self.assertEqual(caught.exception.reason, "pdf-decode-error")
        self.assertEqual(
            caught.exception.__cause__.__class__.__module__, "PyPDF2.errors"
        )

    def test_s10_password_required(self):
        from PyPDF2 import PdfReader, PdfWriter

        writer = PdfWriter()
        writer.append_pages_from_reader(
            PdfReader(BytesIO(synthetic_pdf([[(20, 700, "Protected 17")]])))
        )
        writer.encrypt("synthetic-test-only")
        buffer = BytesIO()
        writer.write(buffer)
        with self.assertRaisesRegex(SourceQualificationError, "password-required"):
            self.observe(buffer.getvalue())

    def test_s11_mislabelled_pdf(self):
        with self.assertRaisesRegex(SourceQualificationError, "unsupported-format"):
            self.observe(b"<html>not a PDF</html>")

    def test_empty_native_page_is_review_not_ocr_support(self):
        result = self.observe(synthetic_pdf([[]]))
        self.assertEqual(result.pages, ("",))
        self.assertEqual(result.disposition, "needs-review")
