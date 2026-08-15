"""Focused cross-platform tests for fail-closed artifact byte reads."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from millefeuille.domain import secure_io
from millefeuille.domain.millefeuille import MillefeuilleContractError


def _stat_like(
    source: os.stat_result,
    *,
    inode: int | None = None,
    size: int | None = None,
    mtime_ns: int | None = None,
    ctime_ns: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        st_dev=source.st_dev,
        st_ino=source.st_ino if inode is None else inode,
        st_mode=source.st_mode,
        st_nlink=source.st_nlink,
        st_size=source.st_size if size is None else size,
        st_mtime_ns=source.st_mtime_ns if mtime_ns is None else mtime_ns,
        st_ctime_ns=source.st_ctime_ns if ctime_ns is None else ctime_ns,
        st_file_attributes=getattr(source, "st_file_attributes", 0),
        st_reparse_tag=getattr(source, "st_reparse_tag", 0),
    )


class TestMillefeuilleSecureIo(unittest.TestCase):
    def test_portable_byte_read_is_an_exact_binary_round_trip(self):
        payload = b"line-one\r\nline-two\ncontrol-z:\x1a\x00\xff" + bytes(range(256))
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(payload)

            with mock.patch.object(
                secure_io,
                "_supports_no_follow",
                return_value=False,
            ):
                actual = secure_io.read_bytes_no_follow(path, "binary artifact")

        self.assertEqual(actual, payload)

    def test_regular_file_read_flags_include_binary_mode_when_available(self):
        binary_flag = 1 << 27
        with mock.patch.object(
            secure_io.os,
            "O_BINARY",
            binary_flag,
            create=True,
        ):
            flags = secure_io._regular_file_read_flags(no_follow=False)

        self.assertEqual(flags & binary_flag, binary_flag)

    def test_windows_portable_read_uses_write_excluding_handle(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            payload = b"stable windows bytes"
            path.write_bytes(payload)
            expected_stat = path.stat()
            fd = os.open(path, os.O_RDONLY)

            with (
                mock.patch.object(secure_io, "_WINDOWS", True),
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io,
                    "_open_windows_locked_regular_file_fd",
                    return_value=(fd, expected_stat),
                ) as locked_open,
            ):
                actual = secure_io.read_bytes_no_follow(path, "windows artifact")

        self.assertEqual(actual, payload)
        locked_open.assert_called_once_with(
            path,
            label="windows artifact",
            expected_stat=mock.ANY,
        )

    def test_windows_ctime_drift_does_not_change_stability_snapshot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"stable")
            before = path.stat()
        after = _stat_like(before, ctime_ns=before.st_ctime_ns + 1)

        self.assertEqual(
            secure_io._stat_object_identity(before),
            secure_io._stat_object_identity(after),
        )
        with mock.patch.object(secure_io, "_WINDOWS", True):
            self.assertEqual(
                secure_io._stat_stability_snapshot(before),
                secure_io._stat_stability_snapshot(after),
            )

    def test_posix_ctime_drift_remains_a_mutation_signal(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"stable")
            before = path.stat()
        after = _stat_like(before, ctime_ns=before.st_ctime_ns + 1)

        with mock.patch.object(secure_io, "_WINDOWS", False):
            self.assertNotEqual(
                secure_io._stat_stability_snapshot(before),
                secure_io._stat_stability_snapshot(after),
            )

    def test_portable_read_rejects_post_open_path_replacement(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            path = root / "artifact.bin"
            replacement = root / "replacement.bin"
            payload = b"same-size"
            path.write_bytes(payload)
            replacement.write_bytes(payload)
            expected = path.stat()
            replaced = _stat_like(expected, inode=replacement.stat().st_ino)
            real_inspect = secure_io._portable_lstat_regular_file
            inspections = 0

            def inspect(candidate: Path, *, label: str) -> os.stat_result:
                nonlocal inspections
                inspections += 1
                if inspections == 3:
                    return replaced
                return real_inspect(candidate, label=label)

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io,
                    "_portable_lstat_regular_file",
                    side_effect=inspect,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "changed while read",
                ),
            ):
                secure_io.read_bytes_no_follow(path, "replaceable artifact")

        self.assertEqual(inspections, 3)

    def test_portable_read_rejects_in_place_mutation_snapshot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"stable")
            expected = path.stat()
            mutated = _stat_like(
                expected,
                size=expected.st_size + 1,
                mtime_ns=expected.st_mtime_ns + 1,
            )
            real_inspect = secure_io._portable_lstat_regular_file
            inspections = 0

            def inspect(candidate: Path, *, label: str) -> os.stat_result:
                nonlocal inspections
                inspections += 1
                if inspections == 3:
                    return mutated
                return real_inspect(candidate, label=label)

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io,
                    "_portable_lstat_regular_file",
                    side_effect=inspect,
                ),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "changed while read",
                ),
            ):
                secure_io.read_bytes_no_follow(path, "mutable artifact")

    def test_portable_read_keeps_the_exact_size_gate(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"complete")
            reads = iter((b"short", b""))

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(secure_io.os, "read", side_effect=reads),
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "changed while read",
                ),
            ):
                secure_io.read_bytes_no_follow(path, "truncated artifact")

    def test_portable_read_fails_closed_without_stable_inode_identity(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(b"stable")
            unavailable = _stat_like(path.stat(), inode=0)

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io,
                    "_portable_lstat_regular_file",
                    return_value=unavailable,
                ),
                mock.patch.object(secure_io.os, "open") as open_file,
                self.assertRaisesRegex(
                    MillefeuilleContractError,
                    "does not expose stable file identity",
                ),
            ):
                secure_io.read_bytes_no_follow(path, "identity-less artifact")

        open_file.assert_not_called()

    def test_windows_reparse_points_are_rejected_as_read_targets(self):
        anchor_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=0,
            st_reparse_tag=0,
        )
        target_stat = SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_file_attributes=secure_io._WINDOWS_REPARSE_POINT,
            st_reparse_tag=0,
        )
        with (
            mock.patch.object(secure_io, "_WINDOWS", True),
            mock.patch.object(
                secure_io.os,
                "lstat",
                side_effect=(anchor_stat, target_stat),
            ),
            self.assertRaisesRegex(
                MillefeuilleContractError,
                "symbolic link or reparse point",
            ),
        ):
            secure_io._portable_lstat_regular_file(
                Path("artifact.bin"),
                label="reparse artifact",
            )

    def test_windows_reparse_points_are_rejected_in_nested_parent_paths(self):
        anchor_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=0,
            st_reparse_tag=0,
        )
        reparse_parent_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=secure_io._WINDOWS_REPARSE_POINT,
            st_reparse_tag=0,
        )
        with (
            mock.patch.object(secure_io, "_WINDOWS", True),
            mock.patch.object(
                secure_io.os,
                "lstat",
                side_effect=(anchor_stat, reparse_parent_stat),
            ) as inspect,
            self.assertRaisesRegex(
                MillefeuilleContractError,
                "path must not contain symbolic links, reparse points",
            ),
        ):
            secure_io._portable_lstat_regular_file(
                Path("parent") / "artifact.bin",
                label="nested reparse artifact",
            )

        self.assertEqual(
            inspect.call_args_list,
            [mock.call(Path(".")), mock.call(Path("parent"))],
        )

    def test_relative_portable_read_rejects_a_reparse_point_anchor(self):
        reparse_anchor_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=secure_io._WINDOWS_REPARSE_POINT,
            st_reparse_tag=0,
        )
        with (
            mock.patch.object(secure_io, "_WINDOWS", True),
            mock.patch.object(
                secure_io.os,
                "lstat",
                return_value=reparse_anchor_stat,
            ) as inspect,
            self.assertRaisesRegex(
                MillefeuilleContractError,
                "path must not contain symbolic links, reparse points",
            ),
        ):
            secure_io._portable_lstat_regular_file(
                Path("artifact.bin"),
                label="relative reparse artifact",
            )

        inspect.assert_called_once_with(Path("."))

    @unittest.skipUnless(
        os.name == "nt",
        "requires Windows kernel share-mode enforcement",
    )
    def test_windows_read_blocks_same_size_mtime_preserving_mutation(self):
        payload = b"A" * 131072
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "artifact.bin"
            path.write_bytes(payload)
            before = path.stat()
            real_read = os.read
            mutation_attempts: list[str] = []

            def read_with_mutation_attempt(fd: int, size: int) -> bytes:
                chunk = real_read(fd, size)
                if not mutation_attempts:
                    try:
                        with path.open("r+b", buffering=0) as stream:
                            stream.seek(0)
                            stream.write(b"B" * len(payload))
                        os.utime(
                            path,
                            ns=(before.st_atime_ns, before.st_mtime_ns),
                        )
                    except OSError as exc:
                        if (
                            getattr(exc, "winerror", None) not in {5, 32, 33}
                            and exc.errno != errno.EACCES
                        ):
                            raise
                        mutation_attempts.append("blocked")
                    else:
                        mutation_attempts.append("mutated")
                return chunk

            with (
                mock.patch.object(
                    secure_io,
                    "_supports_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    secure_io.os,
                    "read",
                    side_effect=read_with_mutation_attempt,
                ),
            ):
                actual = secure_io.read_bytes_no_follow(path, "locked artifact")

        self.assertEqual(mutation_attempts, ["blocked"])
        self.assertEqual(actual, payload)


if __name__ == "__main__":
    unittest.main()
