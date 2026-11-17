"""Operation-local held-directory regression coverage."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from millefeuille.domain import secure_io as io
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error


class HeldDirectories(unittest.TestCase):
    def test_reuse_and_fresh_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "file"
            p.write_bytes(b"a")
            with (
                io.held_directory_reads(),
                mock.patch.object(
                    io,
                    "_open_relative_directory_fd",
                    wraps=io._open_relative_directory_fd,
                ) as opened,
            ):
                self.assertEqual(io.read_bytes_no_follow(p, "test"), b"a")
                count = opened.call_count
                p.write_bytes(b"b")
                self.assertEqual(io.read_bytes_no_follow(p, "test"), b"b")
                self.assertEqual(opened.call_count, count)

    def test_ancestor_replacement_and_symlink(self):
        for link in (False, True):
            with self.subTest(link=link), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                d = root / "directory"
                d.mkdir()
                p = d / "file"
                p.write_bytes(b"a")
                with io.held_directory_reads():
                    io.read_bytes_no_follow(p, "test")
                    d.rename(root / "old")
                    if link:
                        d.symlink_to(root / "old", target_is_directory=True)
                    else:
                        d.mkdir()
                        p.write_bytes(b"a")
                    with self.assertRaises(Error):
                        io.read_bytes_no_follow(p, "test")

    def test_leaf_symlink_and_hardlink_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "file"
            p.write_bytes(b"a")
            alias = Path(tmp) / "alias"
            with io.held_directory_reads():
                io.read_bytes_no_follow(p, "test")
                os.link(p, alias)
                original = io._read_opened_file_bytes

                def mutate(*args, **kwargs):
                    result = original(*args, **kwargs)
                    alias.write_bytes(b"changed")
                    return result

                with (
                    mock.patch.object(
                        io, "_read_opened_file_bytes", side_effect=mutate
                    ),
                    self.assertRaises(Error),
                ):
                    io.read_bytes_no_follow(p, "test")
                p.unlink()
                p.symlink_to(alias)
                with self.assertRaises(Error):
                    io.read_bytes_no_follow(p, "test")

    def test_exception_closes_and_nested_scopes_isolate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "file"
            p.write_bytes(b"a")
            with (
                self.assertRaisesRegex(RuntimeError, "stop"),
                io.held_directory_reads(),
            ):
                io.read_bytes_no_follow(p, "test")
                outer = dict(io._DIRECTORY_READ_SCOPE.get())
                with io.held_directory_reads():
                    io.read_bytes_no_follow(p, "test")
                    inner = dict(io._DIRECTORY_READ_SCOPE.get())
                    self.assertTrue(set(outer.values()).isdisjoint(inner.values()))
                for fd in inner.values():
                    with self.assertRaises(OSError):
                        os.fstat(fd)
                raise RuntimeError("stop")
            self.assertIsNone(io._DIRECTORY_READ_SCOPE.get())
            for fd in outer.values():
                with self.assertRaises(OSError):
                    os.fstat(fd)
