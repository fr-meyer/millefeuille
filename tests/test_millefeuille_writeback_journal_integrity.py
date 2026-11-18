"""Actual SQLite/filesystem restart and drift tests, synthetic authority only."""

import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from millefeuille.domain.writeback_core_journal import _Journal, _provision_journal
from tests import test_millefeuille_writeback_core as core


class JournalIntegrityTests(unittest.TestCase):
    setUp = core.WritebackCoreTests.setUp
    makeplan = core.WritebackCoreTests.makeplan
    authority = core.WritebackCoreTests.authority
    execute = core.WritebackCoreTests.execute

    def test_valid_reopen_and_new_process_replay(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        self.journal.consume(self.plan, self.authority())
        self.journal = _Journal(self.journal.path, self.commitment)
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.consume(self.plan, self.authority())
        external = self.journal.path.parent.parent / "trusted-commitment.json"
        external.write_text(json.dumps(self.commitment))
        external.chmod(0o600)
        payload = {
            "path": str(self.journal.path),
            "commitment_path": str(external),
            "plan": self.plan.payload.decode(),
            "authority": self.authority().__dict__,
        }
        script = """
import json,sys,sqlite3
from pathlib import Path
from millefeuille.domain.writeback_core import _Plan,_Authority
from millefeuille.domain.writeback_core_journal import _Journal
p=json.load(sys.stdin)
j=_Journal(p['path'],json.loads(Path(p['commitment_path']).read_text()))
try:j.consume(_Plan(p['plan'].encode()),_Authority(**p['authority']))
except sqlite3.IntegrityError:sys.exit(0)
sys.exit(19)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_empty_and_valid_replacement_fail_new_open(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        self.journal.consume(self.plan, self.authority())
        path = self.journal.path
        saved = path.with_suffix(".retained")
        path.rename(saved)
        with self.assertRaises((ValueError, OSError)):
            _Journal(path, self.commitment)
        path.touch(mode=0o600)
        with self.assertRaises((ValueError, OSError)):
            _Journal(path, self.commitment)
        path.unlink()
        other = _provision_journal(path)
        self.assertNotEqual(other["journal_id"], self.commitment["journal_id"])
        with self.assertRaises((ValueError, OSError)):
            _Journal(path, self.commitment)
        path.unlink()
        saved.rename(path)
        with self.assertRaises(sqlite3.IntegrityError):
            _Journal(path, self.commitment).consume(self.plan, self.authority())
        self.assertEqual(self.calls, [])

    def test_provision_existing_and_wrong_commitment_denied(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=False)
            return
        with self.assertRaises(FileExistsError):
            _provision_journal(self.journal.path)
        for change in (
            {"journal_id": "0" * 32},
            {"version": 2},
            {"file_identity": [0, 0]},
        ):
            with self.assertRaises((ValueError, sqlite3.Error)):
                _Journal(self.journal.path, {**self.commitment, **change})

    def test_incompatible_schema_and_journal_id_denied(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        for statement in (
            "CREATE TABLE unexpected (x)",
            "UPDATE metadata SET journal_id='wrong'",
        ):
            with self.subTest(statement=statement):
                with sqlite3.connect(self.journal.path) as db:
                    db.execute(statement)
                with self.assertRaises(ValueError):
                    self.execute()
                with sqlite3.connect(self.journal.path) as db:
                    if statement.startswith("CREATE"):
                        db.execute("DROP TABLE unexpected")
                    else:
                        db.execute(
                            "UPDATE metadata SET journal_id=?",
                            (self.commitment["journal_id"],),
                        )
        self.assertEqual(self.calls, [])

    def test_mode_and_hardlink_drift_deny_before_effect(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        path = self.journal.path
        path.chmod(0o644)
        try:
            with self.assertRaises(ValueError):
                self.execute()
        finally:
            path.chmod(0o600)
        alias = path.with_suffix(".alias")
        os.link(path, alias)
        try:
            with self.assertRaises(ValueError):
                self.execute()
        finally:
            alias.unlink()
        self.assertEqual(self.calls, [])

    def test_parent_permission_replacement_and_symlink_deny(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        parent = self.journal.path.parent
        parent.chmod(0o755)
        try:
            with self.assertRaises(ValueError):
                self.execute()
        finally:
            parent.chmod(0o700)
        saved = parent.with_name("retained-parent")
        parent.rename(saved)
        parent.mkdir(mode=0o700)
        try:
            with self.assertRaises((ValueError, OSError)):
                self.execute()
        finally:
            parent.rmdir()
        parent.symlink_to(saved, target_is_directory=True)
        try:
            with self.assertRaises(OSError):
                self.execute()
        finally:
            parent.unlink()
            saved.rename(parent)
        self.assertEqual(self.calls, [])

    def test_sync_time_drift_never_dispatches(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        path = self.journal.path
        original = os.fsync
        calls = 0

        def drift(fd):
            nonlocal calls
            calls += 1
            if calls == 1:
                path.chmod(0o644)
            return original(fd)

        try:
            with patch("os.fsync", side_effect=drift), self.assertRaises(ValueError):
                self.execute()
        finally:
            path.chmod(0o600)
        self.assertEqual(self.calls, [])
        with self.assertRaises(sqlite3.IntegrityError):
            _Journal(path, self.commitment).consume(self.plan, self.authority())

    def test_failed_provision_leaves_quarantine(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=False, reopen=False)
            return
        path = self.journal.path.with_name("quarantine.sqlite3")
        with (
            patch("os.fsync", side_effect=OSError("dummy")),
            self.assertRaises(OSError),
        ):
            _provision_journal(path)
        self.assertTrue(path.exists())
        with self.assertRaises(FileExistsError):
            _provision_journal(path)

    def test_sync_link_inode_and_parent_drift_preserves_consumption(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        for kind in ("link", "inode", "parent-mode"):
            with self.subTest(kind=kind):
                f = core.WritebackCoreTests()
                f.setUp()
                self.addCleanup(f.doCleanups)
                path = f.journal.path
                alias = path.with_suffix(".retained")
                original = os.fsync
                count = 0

                def drift(fd, kind=kind, path=path, alias=alias, original=original):
                    nonlocal count
                    count += 1
                    if count == 1:
                        if kind == "link":
                            os.link(path, alias)
                        elif kind == "inode":
                            path.rename(alias)
                            path.touch(mode=0o600)
                        else:
                            path.parent.chmod(0o755)
                    return original(fd)

                try:
                    with (
                        patch("os.fsync", side_effect=drift),
                        self.assertRaises((ValueError, OSError)),
                    ):
                        f.execute()
                finally:
                    if kind == "link":
                        alias.unlink()
                    elif kind == "inode":
                        path.unlink()
                        alias.rename(path)
                    else:
                        path.parent.chmod(0o700)
                self.assertEqual(f.calls, [])
                with self.assertRaises(sqlite3.IntegrityError):
                    _Journal(path, f.commitment).consume(f.plan, f.authority())

    def test_pre_dispatch_drift_cannot_mutate(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        original = self.journal.event

        def changed(plan, authority, kind, payload):
            original(plan, authority, kind, payload)
            if kind == "mutation-intent":
                self.journal.path.chmod(0o644)

        try:
            with (
                patch.object(self.journal, "event", side_effect=changed),
                self.assertRaises(ValueError),
            ):
                self.execute()
        finally:
            self.journal.path.chmod(0o600)
        self.assertEqual(self.calls, [])

    def test_file_symlink_and_uid_mismatch_deny(self):
        if os.name == "nt":
            core.assert_journal_unavailable(self, existing=True, reopen=True)
            return
        path = self.journal.path
        retained = path.with_suffix(".retained")
        path.rename(retained)
        path.symlink_to(retained)
        try:
            with self.assertRaises(OSError):
                self.execute()
        finally:
            path.unlink()
            retained.rename(path)
        with (
            patch("os.geteuid", return_value=os.geteuid() + 1),
            self.assertRaises(ValueError),
        ):
            self.execute()
        self.assertEqual(self.calls, [])
