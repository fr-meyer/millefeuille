"""Real descriptor operations in synthetic UID metadata fixtures, no root needed."""

import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from millefeuille.domain.writeback_live_files import protected_read


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "evidence"
        self.path.write_bytes(b"authentic-fixture")
        self.path.chmod(0o600)
        self.real_fstat = os.fstat

    def synthetic_ancestor_ownership(self, fd):
        info = self.real_fstat(fd)
        # Only ancestor uid/mode mocked; actual file metadata/read/identity untouched.
        if stat.S_ISDIR(info.st_mode):
            return SimpleNamespace(st_uid=os.geteuid(), st_mode=stat.S_IFDIR | 0o755)
        return info

    def test_positive_and_permissions_hardlink_symlink_size(self):
        with patch("os.fstat", side_effect=self.synthetic_ancestor_ownership):
            self.assertEqual(
                protected_read(self.path, uid=os.geteuid()), b"authentic-fixture"
            )
            with self.assertRaises(ValueError):
                protected_read(self.path, uid=os.geteuid(), limit=2)
            self.path.chmod(0o644)
            with self.assertRaises(ValueError):
                protected_read(self.path, uid=os.geteuid())
            self.path.chmod(0o600)
            link = self.path.with_name("hard")
            os.link(self.path, link)
            with self.assertRaises(ValueError):
                protected_read(self.path, uid=os.geteuid())
            link.unlink()
            link.symlink_to(self.path)
            with self.assertRaises(OSError):
                protected_read(link, uid=os.geteuid())

    def test_replacement_during_read_denied(self):
        actual_read = os.read
        swapped = False

        def racing_read(fd, size):
            nonlocal swapped
            data = actual_read(fd, size)
            if not swapped:
                swapped = True
                replacement = self.path.with_name("replacement")
                replacement.write_bytes(b"changed")
                replacement.chmod(0o600)
                replacement.replace(self.path)
            return data

        with (
            patch("os.fstat", side_effect=self.synthetic_ancestor_ownership),
            patch("os.read", side_effect=racing_read),
            self.assertRaises(ValueError),
        ):
            protected_read(self.path, uid=os.geteuid())

    def test_real_untrusted_ancestor_denied(self):
        with self.assertRaises(ValueError):
            protected_read(self.path, uid=os.geteuid())
