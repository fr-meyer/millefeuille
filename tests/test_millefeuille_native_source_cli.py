"""Original fixture tests through the packaged module entrypoint."""

import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.cli.native_source import run_native_source_cli
from tests.platform_capabilities import requires_linux_native_isolation
from tests.test_millefeuille_real_source_qualification import synthetic_pdf


class NativeSourceCLITests(unittest.TestCase):
    def invoke(self, raw, *flags):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "source.pdf"
            path.write_bytes(raw)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "millefeuille",
                    "inspect-native",
                    "--pdf",
                    str(path),
                    "--sha256",
                    hashlib.sha256(raw).hexdigest(),
                    "--format",
                    "json",
                    *flags,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(list(Path(root).iterdir()), [path])
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout), result.stdout

    @requires_linux_native_isolation
    def test_default_metadata_and_explicit_full_text(self):
        raw = synthetic_pdf(
            [
                [(20, 700, "First 17.25"), (20, 680, "Second 42")],
                [(20, 700, "Page two 99")],
            ]
        )
        code, result, stdout = self.invoke(raw, "--trusted-original-fixture")
        self.assertEqual(code, 0)
        self.assertNotIn("First", stdout)
        self.assertNotIn("text", result["pages"][0])
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["pages"][1]["page"], 2)
        self.assertEqual(
            result["pages"][0]["source_sha256"], hashlib.sha256(raw).hexdigest()
        )
        code, result, _ = self.invoke(
            raw, "--trusted-original-fixture", "--include-text"
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            [p["text"] for p in result["pages"]],
            ["First 17.25\nSecond 42", "Page two 99"],
        )

    @requires_linux_native_isolation
    def test_empty_and_mixed_pages_need_ocr_not_success(self):
        for pages in ([[]], [[(20, 700, "Native 17")], []]):
            code, result, _ = self.invoke(
                synthetic_pdf(pages), "--trusted-original-fixture"
            )
            self.assertEqual(code, 0)
            self.assertEqual(result["disposition"], "needs-review")
            self.assertEqual(result["pages"][-1]["route"], "needs-ocr")
            self.assertEqual(result["page_count"], len(pages))

    @requires_linux_native_isolation
    def test_matching_hash_refusals(self):
        from PyPDF2 import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("synthetic-only")
        stream = BytesIO()
        writer.write(stream)
        for raw, reason in [
            (b"%PDF-1.4\nbroken", "pdf-decode-error"),
            (b"<html>not PDF</html>", "unsupported-format"),
            (stream.getvalue(), "password-required"),
        ]:
            code, result, stdout = self.invoke(raw, "--trusted-original-fixture")
            self.assertEqual(code, 2)
            self.assertEqual(result["reason"], reason)
            self.assertNotIn("synthetic-only", stdout)

    @requires_linux_native_isolation
    def test_untrusted_uses_hard_boundary_without_trust_bypass(self):
        code, result, _ = self.invoke(b"anything")
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "unsupported-format")

    def test_missing_optional_decoder(self):
        with (
            patch(
                "millefeuille.cli.native_source.qualify_untrusted_native_source",
                return_value={
                    "outcome": "refused",
                    "reason": "native-decoder-unavailable",
                },
            ),
            patch("builtins.print") as output,
        ):
            self.assertEqual(
                run_native_source_cli(
                    [
                        "--pdf",
                        "unused",
                        "--sha256",
                        "0" * 64,
                        "--trusted-original-fixture",
                    ]
                ),
                2,
            )
        self.assertIn("native-decoder-unavailable", output.call_args.args[0])

    def test_actual_decoder_import_unavailable(self):
        from millefeuille.domain.source_qualification import (
            SourceQualificationError,
            qualify_native_source,
        )

        raw = synthetic_pdf([[(20, 700, "Original")]])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "source.pdf"
            path.write_bytes(raw)
            with (
                patch.dict(sys.modules, {"PyPDF2": None}),
                self.assertRaises(SourceQualificationError) as caught,
            ):
                qualify_native_source(
                    path, expected_sha256=hashlib.sha256(raw).hexdigest()
                )
            self.assertEqual(caught.exception.reason, "native-decoder-unavailable")

    @requires_linux_native_isolation
    def test_input_and_page_limits_refuse(self):
        for raw, reason in [
            (b"%PDF-" + b"x" * (2**20), "native-boundary-failure"),
            (synthetic_pdf([[]] * 33), "page-count-out-of-bounds"),
        ]:
            code, result, _ = self.invoke(raw, "--trusted-original-fixture")
            self.assertEqual(code, 2)
            self.assertEqual(result["reason"], reason)
