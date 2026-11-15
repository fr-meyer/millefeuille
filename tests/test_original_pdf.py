"""Pure/local file contracts only. No decoder process or containment probe."""

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain import secure_io
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.native_decode_boundary import NativeResourceLimits
from millefeuille.domain.original_pdf import bind_original_pages, inspect_original_pdf
from millefeuille.domain.secure_io import copy_verified_file_no_follow


def observation(text="Figure 2 remains unresolved"):
    return {
        "outcome": "needs-review",
        "source_sha256": "a" * 64,
        "limits_enforced": True,
        "child_reaped": True,
        "exit_code": 0,
        "pages": [
            {
                "page": 1,
                "text": text,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        ],
    }


class OriginalPDFTests(unittest.TestCase):
    def test_distinct_identity_and_snapshot(self):
        first = bind_original_pages(observation(), expected_sha256="a" * 64)
        other = observation()
        other["source_sha256"] = "b" * 64
        second = bind_original_pages(other, expected_sha256="b" * 64)
        self.assertEqual(first["extraction_sha256"], second["extraction_sha256"])
        self.assertNotEqual(first["source_sha256"], second["source_sha256"])
        self.assertEqual(first["pages"][0]["source_sha256"], "a" * 64)
        self.assertEqual(first["disposition"], "needs-review")

    def test_partial_conflict_and_malformed_refused(self):
        for key, value in [
            ("outcome", "partial"),
            ("source_sha256", "b" * 64),
            ("limits_enforced", False),
            ("child_reaped", False),
            ("exit_code", 1),
            ("pages", []),
            ("pages", [{}]),
        ]:
            with self.subTest(key=key, value=value):
                candidate = observation()
                candidate[key] = value
                with self.assertRaises(MillefeuilleContractError):
                    bind_original_pages(candidate, expected_sha256="a" * 64)

    def test_page_binding_and_output_limits(self):
        cases = []
        for key, value in [("page", True), ("page", 2), ("text_sha256", "0" * 64)]:
            candidate = observation()
            candidate["pages"][0][key] = value
            cases.append(candidate)
        candidate = observation()
        candidate["pages"] *= 33
        cases.extend([candidate, observation("x" * 131073), observation("é" * 65536)])
        for candidate in cases:
            with self.assertRaises(MillefeuilleContractError):
                bind_original_pages(candidate, expected_sha256="a" * 64)
        self.assertEqual(
            len(
                bind_original_pages(
                    observation("x" * 131072), expected_sha256="a" * 64
                )["pages"]
            ),
            1,
        )

    def test_unsupported_route_no_spawn(self):
        with (
            patch("millefeuille.domain.native_decode_boundary.sys.platform", "win32"),
            patch(
                "millefeuille.domain.native_decode_boundary.subprocess.Popen",
                side_effect=AssertionError("must not spawn"),
            ),
        ):
            result = inspect_original_pdf({"path": "/never-read", "sha256": "a" * 64})
        self.assertEqual(result["outcome"], "refused")
        self.assertEqual(result["charged_read_bytes"], 0)
        self.assertEqual(NativeResourceLimits().max_input_bytes, 1048576)

    def assert_streaming_refused_before_io(self):
        with (
            patch.object(secure_io, "_open_regular_file_fd") as open_source,
            patch.object(secure_io.os, "write") as write_destination,
            patch.object(secure_io.os, "fstat") as stat_destination,
        ):
            deadline = unittest.mock.Mock()
            with self.assertRaisesRegex(
                MillefeuilleContractError, "unsupported streaming source policy"
            ):
                copy_verified_file_no_follow(
                    "/never-read",
                    -1,
                    expected_sha256="a" * 64,
                    max_bytes=16777216,
                    check_deadline=deadline,
                )
            open_source.assert_not_called()
            write_destination.assert_not_called()
            stat_destination.assert_not_called()
            deadline.assert_not_called()

    def test_missing_nofollow_refuses_streaming_before_io(self):
        # Remove a capability only; never emulate successful secure Windows I/O.
        with patch.object(secure_io, "_supports_no_follow", return_value=False):
            self.assert_streaming_refused_before_io()

    @unittest.skipIf(secure_io._supports_no_follow(), "native O_NOFOLLOW available")
    def test_native_missing_nofollow_refuses_streaming_before_io(self):
        self.assert_streaming_refused_before_io()

    def transfer(self, path, destination, cap, expected, deadline=lambda: None):
        return copy_verified_file_no_follow(
            path,
            destination.fileno(),
            expected_sha256=expected,
            max_bytes=cap,
            check_deadline=deadline,
        )

    @unittest.skipUnless(
        secure_io._supports_no_follow(), "streaming custody requires O_NOFOLLOW"
    )
    def test_stream_complete_above_legacy_and_exact_cap(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryFile() as dest:
            source = Path(root) / "synthetic.pdf"
            # Synthetic bytes test custody, deliberately not parser qualification.
            raw = b"%PDF-synthetic\n" + b"x" * 1048576
            source.write_bytes(raw)
            actual_read = os.read
            requests = []

            def bounded_read(fd, count):
                requests.append(count)
                return actual_read(fd, count)

            with patch("millefeuille.domain.secure_io.os.read", bounded_read):
                size, digest = self.transfer(
                    source, dest, len(raw), hashlib.sha256(raw).hexdigest()
                )
            self.assertEqual(size, len(raw))
            self.assertLessEqual(max(requests), 65536)
            dest.seek(0)
            self.assertEqual(hashlib.sha256(dest.read()).hexdigest(), digest)

    @unittest.skipUnless(
        secure_io._supports_no_follow(), "streaming custody requires O_NOFOLLOW"
    )
    def test_oversize_empty_hash_and_deadline(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryFile() as dest:
            source = Path(root) / "synthetic.pdf"
            for raw, cap, expected in [
                (b"", 1, "0" * 64),
                (b"xx", 1, "0" * 64),
                (b"x", 1, "0" * 64),
            ]:
                source.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.transfer(source, dest, cap, expected)
            with self.assertRaises(TimeoutError):
                self.transfer(
                    source,
                    dest,
                    1,
                    "0" * 64,
                    lambda: (_ for _ in ()).throw(TimeoutError()),
                )

    @unittest.skipUnless(
        secure_io._supports_no_follow(), "streaming custody requires O_NOFOLLOW"
    )
    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryFile() as dest:
            source = Path(root) / "file"
            source.write_bytes(b"x")
            link = Path(root) / "link"
            link.symlink_to(source)
            with self.assertRaises(MillefeuilleContractError):
                self.transfer(link, dest, 1, hashlib.sha256(b"x").hexdigest())

    @unittest.skipUnless(
        secure_io._supports_no_follow(), "streaming custody requires O_NOFOLLOW"
    )
    def test_mutation_after_read_refused(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryFile() as dest:
            source = Path(root) / "file"
            source.write_bytes(b"x")
            actual_read = os.read

            def mutate(fd, count):
                data = actual_read(fd, count)
                source.write_bytes(b"changed-size")
                return data

            with (
                patch("millefeuille.domain.secure_io.os.read", mutate),
                self.assertRaises(ValueError),
            ):
                self.transfer(source, dest, 1, hashlib.sha256(b"x").hexdigest())

    @unittest.skipUnless(
        secure_io._supports_no_follow(), "streaming custody requires O_NOFOLLOW"
    )
    def test_exact_16mib_and_one_byte_over(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryFile() as dest:
            source = Path(root) / "synthetic"
            block = b"x" * 65536
            expected = hashlib.sha256()
            with source.open("wb") as output:
                for _ in range(256):
                    output.write(block)
                    expected.update(block)
            self.assertEqual(
                self.transfer(source, dest, 16777216, expected.hexdigest())[0], 16777216
            )
            with source.open("ab") as output:
                output.write(b"x")
            with self.assertRaisesRegex(ValueError, "source-size-out-of-bounds"):
                self.transfer(source, dest, 16777216, expected.hexdigest())

    @unittest.skipUnless(
        secure_io._supports_no_follow(), "streaming custody requires O_NOFOLLOW"
    )
    def test_short_read_and_partial_write(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryFile() as dest:
            source = Path(root) / "synthetic"
            source.write_bytes(b"abcdef")
            expected = hashlib.sha256(b"abcdef").hexdigest()
            with (
                patch("millefeuille.domain.secure_io.os.read", return_value=b""),
                self.assertRaisesRegex(ValueError, "short-source-read"),
            ):
                self.transfer(source, dest, 6, expected)
            actual_write = os.write
            with patch(
                "millefeuille.domain.secure_io.os.write",
                lambda fd, data: actual_write(fd, data[:1]),
            ):
                self.assertEqual(self.transfer(source, dest, 6, expected)[0], 6)
            dest.seek(0)
            self.assertEqual(dest.read(), b"abcdef")
