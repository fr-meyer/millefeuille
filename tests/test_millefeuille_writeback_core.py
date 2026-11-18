"""Offline deterministic core tests: no live/root boundary is asserted."""

import copy
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from millefeuille.domain.writeback_core import _Authority, _hash, _plan
from millefeuille.domain.writeback_core_journal import (
    _Journal,
    _provision_journal,
    _run,
)
from millefeuille.domain.writeback_http import _Acknowledgement, _project
from tests.test_millefeuille_classification_actions import (
    _prepare_classified_run,
    action_lock_fields,
)


def receipt_projection_fixture(test):
    """Plain inputs for receipt/proof leaf tests, NOT an accepted writeback plan.

    Reuse existing source fixture identity and raw snapshot. No acceptance flags,
    materialization bundle, journal, or authenticated evidence are constructed.
    """
    from types import SimpleNamespace

    from millefeuille.domain.writeback_core import _Plan, _snapshot, _wire
    from tests.test_millefeuille_stage_cli import RUN_ID, _prepare_fixture_run
    temp = tempfile.TemporaryDirectory(prefix="mf-proof-leaf-")
    test.addCleanup(temp.cleanup)
    _, run = _prepare_fixture_run(temp.name)
    index = json.loads((run / "artifact-index.json").read_text())
    before = WritebackCoreTests.snapshot_fixture()
    mapping = {"key": "DEST1234"}
    plan = _Plan(_wire({
        "run_id": RUN_ID,
        "source_hash": index["source_pack"]["source_hash"],
        "source_item_version": 7,
        "before": _snapshot(before),
        "mapping": mapping,
    }))
    return SimpleNamespace(temp=temp, before=before, plan=plan, mapping=mapping)


def assert_journal_unavailable(test, *, existing=False, reopen=False):
    """No database, worker or transport may start without protected custody."""
    from millefeuille.domain import writeback_core_journal as journal
    from millefeuille.domain.millefeuille import MillefeuilleContractError
    root = Path(test.temp.name)
    path = root / "operations.sqlite3"
    scenario = test._testMethodName
    if existing:
        # Untrusted existing bytes are deliberately not an accepted SQLite journal.
        path.write_bytes(b"existing-journal-must-not-be-adopted")
        if scenario == "test_missing_empty_and_valid_replacement_fail_new_open":
            path.write_bytes(b"")
        elif scenario == "test_mode_and_hardlink_drift_deny_before_effect":
            path.chmod(0o644)
        elif scenario == "test_parent_permission_replacement_and_symlink_deny":
            root.chmod(0o755)
        elif scenario == "test_incompatible_schema_and_journal_id_denied":
            path.write_bytes(b"incompatible-schema-and-journal-id")
        elif scenario == "test_sync_link_inode_and_parent_drift_preserves_consumption":
            path.rename(root / "retained-original")
            path.write_bytes(b"replacement-inode")
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    with patch.object(journal.os, "open") as opened, patch.object(
        journal.sqlite3, "connect"
    ) as database, patch.object(journal.os, "fsync") as sync, patch(
        "subprocess.Popen"
    ) as worker, patch("socket.socket") as transport:
        with test.assertRaisesRegex(
            MillefeuilleContractError,
            "^protected journals require POSIX no-follow descriptors$",
        ):
            if reopen:
                with journal._held(path):
                    test.fail("unsupported journal custody entered")
            else:
                journal._provision_journal(path)
        opened.assert_not_called()
        database.assert_not_called()
        sync.assert_not_called()
        worker.assert_not_called()
        transport.assert_not_called()
    test.assertEqual(
        before, {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    )


class WritebackCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mfwb-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        if os.name == "nt" and self._testMethodName not in {
            "test_proposal_wrong_mapping_notes_and_source_denied",
            "test_pure_plan_no_io_and_snapshot_is_immutable",
        }:
            return  # Each durable test explicitly exercises its real refusal below.
        if os.name == "nt":
            from tests.fixture_exports import load_export

            def load(name):
                return json.loads(load_export("classified/" + name))
        else:
            _, run = _prepare_classified_run(self.temp.name)

            def load(name):
                return json.loads((run / name).read_text())

        idx = load("artifact-index.json")
        decision = load("classification/decision-records/zotero-ITEM1.json")
        idx["source_identity"]["zotero_item_key"] = "ABCD1234"
        self.before = WritebackCoreTests.snapshot_fixture()
        self.bundle = {
            "index": idx,
            "manifest": load("stage-manifest.json"),
            "decision": decision,
            "taxonomy_lock": action_lock_fields()["taxonomy_lock"],
            "acceptance": load("reports/acceptance-summary.json"),
            "source": {
                "library_type": "user",
                "library_id": "12345",
                "key": "ABCD1234",
                "source_item_version": 7,
                "source_hash": decision["source_hash"],
            },
        }
        self.mapping = {
            "library_type": "user",
            "library_id": "12345",
            "key": "DEST1234",
            "entry_id": "L2-TRAINING",
            "primary_path": decision["primary_path"],
            "taxonomy_lock_identity": self.bundle["taxonomy_lock"]["content_identity"],
        }
        self.plan = self.makeplan()
        if os.name == "nt":
            self.calls = []
            return  # Pure planning/CLI inputs never acquire POSIX journal custody.
        journal_dir = root / "journal"
        journal_dir.mkdir(mode=0o700)
        self.commitment = _provision_journal(journal_dir / "operations.sqlite3")
        self.journal = _Journal(journal_dir / "operations.sqlite3", self.commitment)
        self.state = copy.deepcopy(self.before)
        self.calls = []
        test = self

        class Reader:
            def read(self, identity, deadline):
                state = copy.deepcopy(test.state)
                raw = json.dumps(
                    {
                        "key": state["key"],
                        "version": state["version"],
                        "library": {
                            "type": state["library_type"],
                            "id": int(state["library_id"]),
                        },
                        "data": {
                            **state["data"],
                            "key": state["key"],
                            "version": state["version"],
                            "dateModified": "2026-11-16T00:00:00Z",
                        },
                    }
                ).encode()
                snapshot, proof = _project(identity, raw, state["version"])
                return {"snapshot": snapshot, "proof": proof}

        class Writer:
            def patch(self, identity, expected_version, delta, deadline):
                with sqlite3.connect(test.journal.path) as db:
                    assert (
                        db.execute(
                            "SELECT COUNT(*) FROM events WHERE kind='mutation-intent'"
                        ).fetchone()[0]
                        > 0
                    )
                    assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] > 0
                test.calls.append((expected_version, copy.deepcopy(delta)))
                if test.state["version"] != expected_version:
                    raise ValueError("412")
                test.state["data"].update(copy.deepcopy(delta))
                test.state["version"] += 1
                return _Acknowledgement(test.state["version"])

        self.reader = Reader()
        self.writer = Writer()

    @staticmethod
    def snapshot_fixture():
        return {
            "library_type": "user",
            "library_id": "12345",
            "key": "ABCD1234",
            "version": 9,
            "data": {
                "itemType": "journalArticle",
                "title": "DUMMY",
                "tags": [{"tag": "unrelated", "type": 1}, {"tag": "remove", "type": 1}],
                "collections": ["KEEP1234"],
                "extra": "preserved",
            },
        }

    def makeplan(self, **changes):
        args = {
            "materialized": self.bundle,
            "before": self.before,
            "collection": self.mapping,
            "add_tags": ("new",),
            "remove_tags": ("remove",),
        }
        args.update(changes)
        return _plan(**args)

    def authority(self, operation="execute", audit=None, receipt="receipt-one"):
        return _Authority(
            receipt,
            _hash(receipt),
            self.plan.digest,
            operation,
            (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            audit,
        )

    def execute(self, **kwargs):
        args = {
            "plan": self.plan,
            "authority": self.authority(),
            "journal": self.journal,
            "writer": self.writer,
            "reader": self.reader,
        }
        args.update(kwargs)
        return _run(**args)

    def test_pure_plan_exact_preservation_and_result_schema(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        from jsonschema import validate

        out = self.execute()
        self.assertEqual(out["result"]["status"], "written")
        schema = json.loads(
            (
                Path(__file__).parents[1]
                / "specs/millefeuille-pipeline/zotero-writeback-result.schema.json"
            ).read_text()
        )
        validate(out["result"], schema)
        self.assertIn({"tag": "unrelated", "type": 1}, self.state["data"]["tags"])
        self.assertEqual(self.state["data"]["extra"], "preserved")
        self.assertEqual(self.state["data"]["collections"], ["DEST1234", "KEEP1234"])
        self.assertEqual(out["result"]["source_item_version"], 7)
        self.assertEqual(out["result"]["item_version"], 10)

    def test_proposal_wrong_mapping_notes_and_source_denied(self):
        for field, value in (("status", "adjudication-required"), ("run_id", "wrong")):
            bundle = copy.deepcopy(self.bundle)
            bundle["decision"][field] = value
            with self.assertRaises(ValueError):
                self.makeplan(materialized=bundle)
        for changes in (
            {"key": "Methods > Training"},
            {"library_type": "group"},
            {"entry_id": "unknown"},
        ):
            with self.assertRaises(ValueError):
                self.makeplan(collection={**self.mapping, **changes})
        with self.assertRaises(ValueError):
            self.makeplan(note_html="<p>DUMMY</p>")
        bundle = copy.deepcopy(self.bundle)
        bundle["source"]["key"] = "WRONG123"
        with self.assertRaises(ValueError):
            self.makeplan(materialized=bundle)
        self.assertEqual(self.calls, [])

    def test_version_conflict_no_mutation_and_no_replay(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        self.state["version"] += 1
        self.assertEqual(self.execute()["result"]["status"], "needs-review")
        self.assertEqual(self.calls, [])
        with self.assertRaises(sqlite3.IntegrityError):
            self.execute()

    def test_applied_reply_lost_is_not_owned_written(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        original = self.writer.patch

        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError()

        with patch.object(self.writer, "patch", side_effect=lost):
            self.assertEqual(self.execute()["result"]["status"], "needs-review")
        self.assertEqual(len(self.calls), 1)

    def test_429_and_readback_mismatch_never_retry(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        with patch.object(
            self.writer, "patch", side_effect=RuntimeError("429")
        ) as mutation:
            self.assertEqual(self.execute()["result"]["status"], "needs-review")
            mutation.assert_called_once()

    def test_partial_or_unrelated_edit_needs_review(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        original = self.writer.patch

        def edit(*args, **kwargs):
            original(*args, **kwargs)
            self.state["data"]["extra"] = "changed"

        with patch.object(self.writer, "patch", side_effect=edit):
            self.assertEqual(self.execute()["result"]["status"], "needs-review")
        self.assertEqual(len(self.calls), 1)

    def test_separate_rollback_restores_only_owned_delta(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        out = self.execute()
        with self.assertRaises(ValueError):
            self.execute(rollback=True)
        rolled = self.execute(
            authority=self.authority("rollback", out["audit_digest"], "rollback-one"),
            rollback=True,
        )
        self.assertEqual(rolled["audit"]["status"], "rolled-back")
        self.assertEqual(self.state["data"], self.plan.value()["before"]["data"])
        self.assertEqual(len(self.calls), 2)

    def test_rollback_concurrent_edit_denied(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        out = self.execute()
        self.state["version"] += 1
        rolled = self.execute(
            authority=self.authority("rollback", out["audit_digest"], "rollback-one"),
            rollback=True,
        )
        self.assertEqual(rolled["audit"]["status"], "needs-review")
        self.assertEqual(len(self.calls), 1)

    def test_fsync_failure_prevents_mutation(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        with (
            patch.object(
                self.journal, "_sync", side_effect=OSError("dummy fsync failure")
            ),
            self.assertRaises(OSError),
        ):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_concurrent_duplicate_consumption_only_one_effect(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        results = []

        def invoke():
            try:
                results.append(self.execute()["result"]["status"])
            except sqlite3.IntegrityError:
                results.append("replay-denied")

        threads = [threading.Thread(target=invoke) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ["written", "replay-denied"])
        self.assertEqual(len(self.calls), 1)

    def test_deadline_after_first_read_prevents_effect(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        with patch(
            "millefeuille.domain.writeback_core.time.monotonic", side_effect=[0, 0, 11]
        ):
            self.assertEqual(self.execute()["result"]["status"], "needs-review")
        self.assertEqual(self.calls, [])

    def test_missing_journal_does_not_recreate_or_dispatch(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        self.journal.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.execute()
        self.assertFalse(self.journal.path.exists())
        self.assertEqual(self.calls, [])

    def test_expired_authority_and_wrong_plan_deny(self):
        if os.name == "nt":
            assert_journal_unavailable(self)
            return
        from dataclasses import replace

        for authority in (
            replace(self.authority(), expires_at="2000-01-01T00:00:00Z"),
            replace(self.authority(), plan_digest=_hash("different")),
        ):
            with self.assertRaises(ValueError):
                self.execute(authority=authority)
        self.assertEqual(self.calls, [])

    def test_pure_plan_no_io_and_snapshot_is_immutable(self):
        with (
            patch("builtins.open", side_effect=AssertionError("no IO")),
            patch("socket.socket", side_effect=AssertionError("no network")),
        ):
            plan = self.makeplan()
        self.before["data"]["title"] = "mutated after planning"
        self.assertEqual(plan.value()["before"]["data"]["title"], "DUMMY")
