"""Actual isolated decoder fixtures, never private sources."""

import hashlib
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain.native_decode_boundary import (
    NativeResourceLimits,
    _inspect_mapped_pdf,
    inspect_bounded_pdf,
)
from tests.test_millefeuille_real_source_qualification import synthetic_pdf


class NativeBoundaryTests(unittest.TestCase):
    def inspect(self, raw):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fixture.pdf"
            path.write_bytes(raw)
            return inspect_bounded_pdf(
                {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
            )

    def test_unsupported_before_input(self):
        with (
            patch("millefeuille.domain.native_decode_boundary.sys.platform", "win32"),
            patch(
                "millefeuille.domain.native_decode_boundary.subprocess.Popen",
                side_effect=AssertionError,
            ),
        ):
            result = inspect_bounded_pdf({"path": "/never-read", "sha256": "0" * 64})
        self.assertFalse(result["limits_enforced"])
        self.assertEqual(result["charged_read_bytes"], 0)

    def test_limit_override_refused_without_spawn(self):
        with patch(
            "millefeuille.domain.native_decode_boundary.subprocess.Popen",
            side_effect=AssertionError,
        ):
            result = inspect_bounded_pdf({}, NativeResourceLimits(cpu_seconds=11))
        self.assertEqual(result["charged_read_bytes"], 0)

    @unittest.skipUnless(
        sys.platform == "linux" and platform.machine() == "x86_64",
        "kernel adapter is Linux x86-64 only",
    )
    def test_actual_valid_pdf_exact_numeric_identity(self):
        raw = synthetic_pdf([[(20, 700, "Value 17.25")], [(20, 700, "Second 99")]])
        result = self.inspect(raw)
        self.assertEqual(result["outcome"], "needs-review", result)
        self.assertEqual(
            [r["text"] for r in result["pages"]], ["Value 17.25", "Second 99"]
        )
        self.assertEqual([r["page"] for r in result["pages"]], [1, 2])
        self.assertTrue(result["limits_enforced"])
        self.assertTrue(result["child_reaped"])
        self.assertEqual(result["source_sha256"], hashlib.sha256(raw).hexdigest())

    @unittest.skipUnless(
        sys.platform == "linux" and platform.machine() == "x86_64",
        "kernel adapter is Linux x86-64 only",
    )
    def test_matching_hash_malformed_and_mislabelled(self):
        for raw, reason in (
            (b"%PDF-1.4 broken", "pdf-decode-error"),
            (b"not a PDF", "unsupported-format"),
        ):
            result = self.inspect(raw)
            self.assertEqual(result["reason"], reason, result)
            self.assertTrue(result["child_reaped"])
            self.assertTrue(result["limits_enforced"])

    @unittest.skipUnless(
        sys.platform == "linux" and platform.machine() == "x86_64",
        "Linux x86-64 kernel policy",
    )
    def test_kernel_denies_fd_fs_network_children_and_file_mappings(self):
        raw = synthetic_pdf([[(20, 700, "Safe 17.25")]])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fixture.pdf"
            path.write_bytes(raw)
            result = _inspect_mapped_pdf(
                {"path": path, "sha256": hashlib.sha256(raw).hexdigest()},
                _probe="syscalls",
            )
        self.assertEqual(result["outcome"], "probe", result)
        self.assertTrue(result["all_denied"])
        self.assertEqual(result["probes"], 8)
        self.assertEqual(result["evidence"]["post_filter_fd_io_bytes"], 0)
        self.assertTrue(result["child_reaped"])

    @unittest.skipUnless(
        sys.platform == "linux" and platform.machine() == "x86_64",
        "Linux x86-64 kernel policy",
    )
    def test_cpu_attack_is_terminated_and_reaped(self):
        raw = b"%PDF-1.4 synthetic spin"
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fixture.pdf"
            path.write_bytes(raw)
            result = _inspect_mapped_pdf(
                {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}, _probe="spin"
            )
        self.assertEqual(result["outcome"], "refused", result)
        self.assertTrue(result["child_reaped"])
        self.assertTrue(result["limits_enforced"])
        self.assertLess(result["evidence"]["child_runtime"], 22)

    @unittest.skipUnless(
        sys.platform == "linux" and platform.machine() == "x86_64",
        "Linux x86-64 kernel policy",
    )
    def test_encrypted_pdf_no_password_recovery(self):
        from io import BytesIO

        import PyPDF2

        writer = PyPDF2.PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("synthetic-password-not-a-credential")
        buffer = BytesIO()
        writer.write(buffer)
        result = self.inspect(buffer.getvalue())
        self.assertEqual(result["reason"], "password-required", result)
        self.assertTrue(result["child_reaped"])

    def test_invalid_hash_manifest_has_no_child_or_source_access(self):
        with patch(
            "millefeuille.domain.native_decode_boundary.subprocess.Popen",
            side_effect=AssertionError,
        ):
            result = inspect_bounded_pdf({"path": "/never-read", "sha256": "invalid"})
        self.assertEqual(result["charged_read_bytes"], 0)

    @unittest.skipUnless(
        sys.platform == "linux" and platform.machine() == "x86_64",
        "Linux x86-64 kernel policy",
    )
    def test_address_space_and_fixed_output_exhaustion_refuse(self):
        for mode in ("memory", "output"):
            raw = b"%PDF-1.4 synthetic resource probe"
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "fixture.pdf"
                path.write_bytes(raw)
                result = _inspect_mapped_pdf(
                    {"path": path, "sha256": hashlib.sha256(raw).hexdigest()},
                    _probe=mode,
                )
            self.assertEqual(result["outcome"], "refused", result)
            self.assertTrue(result["child_reaped"])
            self.assertTrue(result["limits_enforced"])
            self.assertLessEqual(result["charged_write_bytes"], 262144)
