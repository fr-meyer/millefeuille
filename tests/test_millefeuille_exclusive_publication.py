"""Synthetic real-filesystem qualification of the private namespace subset."""

import dataclasses
import json
import os
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
from millefeuille.domain.exclusive_publication import (
    CONTRACT,
    publish_snapshot,
    rollback_snapshot,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError


class InjectedError(RuntimeError):
    pass


@unittest.skipUnless(os.name == "posix", "POSIX private namespace qualification")
class ExclusivePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        (self.root / "nested").mkdir(mode=0o700)
        self.before = {"a": b"old-a", "nested/b": b"old-b"}
        for ref, data in self.before.items():
            self.put(ref, data)
        self.put("unrelated", b"leave alone")
        self.payloads = {"a": b"new-a", "nested/b": b"new-b", "z": b"created"}
        self.outputs = plan_exact_outputs(root=self.root, outputs=self.payloads)

    def put(self, ref, data):
        (self.root / ref).write_bytes(data)
        (self.root / ref).chmod(0o600)

    def publish(self, **changes):
        args = {
            "root": self.root,
            "outputs": self.outputs,
            "approved_refs": tuple(self.payloads),
            "transaction_ref": "txn",
            "namespace_contract": CONTRACT,
            "max_files": 3,
            "max_bytes": 4096,
            "max_aux_paths": 8,
            "max_aux_bytes": 100000,
            "max_journal_bytes": 65536,
        }
        args.update(changes)
        return publish_snapshot(**args)

    def rollback(self, **changes):
        args = {
            "root": self.root,
            "transaction_ref": "txn",
            "namespace_contract": CONTRACT,
            "max_journal_bytes": 65536,
            "max_bytes": 4096,
        }
        args.update(changes)
        return rollback_snapshot(**args)

    def fail_at(self, target):
        def hook(label):
            if label == target:
                raise InjectedError(label)

        return hook

    def assert_before(self):
        for ref, data in self.before.items():
            self.assertEqual((self.root / ref).read_bytes(), data)
        self.assertFalse((self.root / "z").exists())
        self.assertEqual((self.root / "unrelated").read_bytes(), b"leave alone")

    def test_complete_rollback_and_consumed_name(self):
        self.assertEqual(len(self.publish()), 3)
        for ref, data in self.payloads.items():
            self.assertEqual((self.root / ref).read_bytes(), data)
            self.assertEqual((self.root / ref).stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(self.rollback()), 3)
        self.assert_before()
        with self.assertRaises(MillefeuilleContractError):
            self.publish()
        with self.assertRaises(MillefeuilleContractError):
            self.rollback()
        self.assertTrue((self.root / "txn/journal.jsonl").exists())

    def test_fault_matrix(self):
        labels = [
            f"{phase}:{i}"
            for i in range(3)
            for phase in ("before", "intent", "effect", "after")
        ]
        labels += ["before:complete", "after:complete"]
        for label in labels:
            with self.subTest(label=label):
                # Isolated fixture per crash boundary, not a replay.
                fixture = ExclusivePublicationTests()
                fixture.setUp()
                try:
                    with self.assertRaises(InjectedError):
                        fixture.publish(fault=fixture.fail_at(label))
                    if label.startswith(("intent:", "effect:")):
                        journal = (fixture.root / "txn/journal.jsonl").read_bytes()
                        with self.assertRaisesRegex(
                            MillefeuilleContractError, "uncertain"
                        ):
                            fixture.rollback()
                        self.assertEqual(
                            (fixture.root / "txn/journal.jsonl").read_bytes(), journal
                        )
                    else:
                        fixture.rollback()
                        fixture.assert_before()
                    with self.assertRaises(MillefeuilleContractError):
                        fixture.publish()
                finally:
                    fixture.doCleanups()

    def test_identical_bytes_different_inode_refused(self):
        self.publish()
        self.put("replacement", b"new-a")
        os.replace(self.root / "replacement", self.root / "a")
        journal = (self.root / "txn/journal.jsonl").read_bytes()
        with self.assertRaisesRegex(MillefeuilleContractError, "unrelated"):
            self.rollback()
        self.assertEqual((self.root / "txn/journal.jsonl").read_bytes(), journal)
        self.assertEqual((self.root / "z").read_bytes(), b"created")

    def test_caps_and_contract_preflight(self):
        for change in (
            {"max_files": 2},
            {"max_bytes": 1},
            {"max_aux_paths": 7},
            {"max_aux_bytes": 1},
            {"max_journal_bytes": 1},
            {"namespace_contract": ""},
            {"approved_refs": ("a",)},
            {"transaction_ref": "../escape"},
            {"transaction_ref": "a/txn"},
        ):
            with self.subTest(change=change):
                with self.assertRaises((MillefeuilleContractError, OSError)):
                    self.publish(**change)
                self.assertFalse((self.root / "txn").exists())
                self.assert_before()

    def test_stale_hash_and_mode_preflight(self):
        bad = (dataclasses.replace(self.outputs[0], sha256="0" * 64), *self.outputs[1:])
        with self.assertRaises(MillefeuilleContractError):
            self.publish(outputs=bad)
        self.put("a", b"changed")
        with self.assertRaises(MillefeuilleContractError):
            self.publish()
        self.put("a", b"old-a")
        (self.root / "a").chmod(0o644)
        with self.assertRaises(MillefeuilleContractError):
            self.publish()
        self.assertFalse((self.root / "txn").exists())

    def test_symlink_and_unsafe_ancestor_preflight(self):
        (self.root / "nested").chmod(0o755)
        with self.assertRaises(MillefeuilleContractError):
            self.publish()
        (self.root / "nested").chmod(0o700)
        (self.root / "a").unlink()
        (self.root / "a").symlink_to("unrelated")
        with self.assertRaises((MillefeuilleContractError, OSError)):
            self.publish()
        self.assertFalse((self.root / "txn").exists())

    def test_flock_contention(self):
        import fcntl

        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(MillefeuilleContractError, "flock"):
                self.publish()
        finally:
            os.close(fd)
        self.assertFalse((self.root / "txn").exists())

    def test_midflight_identity_drift(self):
        def change(label):
            if label == "before:1":
                self.put("replacement", b"old-b")
                os.replace(self.root / "replacement", self.root / "nested/b")

        with self.assertRaisesRegex(MillefeuilleContractError, "identity"):
            self.publish(fault=change)
        with self.assertRaisesRegex(MillefeuilleContractError, "unrelated"):
            self.rollback()

    def test_journal_receipts(self):
        self.publish()
        events = [
            json.loads(x)
            for x in (self.root / "txn/journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-1]["event"], "complete")
        for event in events:
            if event["event"] == "post":
                self.assertEqual(event["post"]["identity"][2:4], [1, 0o600])
        self.assertEqual((self.root / "txn").stat().st_mode & 0o777, 0o700)

    def test_interrupted_rollback_never_retries(self):
        self.publish()
        with self.assertRaises(InjectedError):
            self.rollback(fault=self.fail_at("rollback-effect:2"))
        with self.assertRaisesRegex(MillefeuilleContractError, "consumed"):
            self.rollback()

    def test_real_process_exit_after_recorded_step_or_uncertain_effect(self):
        for label in ("after:0", "effect:0", "before:complete"):
            with self.subTest(label=label):
                fixture = ExclusivePublicationTests()
                fixture.setUp()
                try:
                    pid = os.fork()
                    if pid == 0:
                        fixture.publish(
                            fault=lambda point, boundary=label: (
                                os._exit(77) if point == boundary else None
                            )
                        )
                        os._exit(78)
                    _, status = os.waitpid(pid, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 77)
                    with self.assertRaises(MillefeuilleContractError):
                        fixture.publish()
                    if label.startswith("effect:"):
                        with self.assertRaisesRegex(
                            MillefeuilleContractError, "uncertain"
                        ):
                            fixture.rollback()
                        self.assertEqual((fixture.root / "a").read_bytes(), b"new-a")
                    else:
                        fixture.rollback()
                        fixture.assert_before()
                finally:
                    fixture.doCleanups()

    def test_malformed_beforeimage_or_aux_path_blocks_entire_rollback(self):
        for mutation in ("bytes", "path", "sequence", "truncate"):
            with self.subTest(mutation=mutation):
                fixture = ExclusivePublicationTests()
                fixture.setUp()
                try:
                    fixture.publish()
                    journal = fixture.root / "txn/journal.jsonl"
                    events = [
                        json.loads(line) for line in journal.read_text().splitlines()
                    ]
                    if mutation == "bytes":
                        events[0]["outputs"][0]["before_base64"] = "Y29ycnVwdA=="
                    elif mutation == "path":
                        events[0]["outputs"][0]["rollback"] = "../outside"
                    elif mutation == "sequence":
                        events.insert(1, {"event": "complete"})
                    raw = (
                        chr(10).join(json.dumps(x) for x in events) + chr(10)
                    ).encode()
                    if mutation == "truncate":
                        raw = raw[:-1]
                    journal.write_bytes(raw)
                    expected = {
                        ref: (fixture.root / ref).read_bytes()
                        for ref in fixture.payloads
                    }
                    with self.assertRaises(MillefeuilleContractError):
                        fixture.rollback()
                    self.assertEqual(journal.read_bytes(), raw)
                    self.assertEqual(
                        expected,
                        {
                            ref: (fixture.root / ref).read_bytes()
                            for ref in fixture.payloads
                        },
                    )
                finally:
                    fixture.doCleanups()

    def test_staging_drift_fails_before_canonical_effect(self):
        def change(label):
            if label == "before:0":
                self.put("txn/stage-0", b"altered")

        with self.assertRaisesRegex(MillefeuilleContractError, "staging"):
            self.publish(fault=change)
        self.assert_before()
        self.rollback()
        self.assert_before()

    def test_terminal_drift_does_not_record_complete(self):
        def change(label):
            if label == "before:complete":
                self.put("a", b"unrelated newer change")

        with self.assertRaisesRegex(MillefeuilleContractError, "terminal"):
            self.publish(fault=change)
        events = [
            json.loads(line)
            for line in (self.root / "txn/journal.jsonl").read_text().splitlines()
        ]
        self.assertNotIn("complete", [x["event"] for x in events])
        with self.assertRaisesRegex(MillefeuilleContractError, "unrelated"):
            self.rollback()

    def swap_nested_parent(self):
        (self.root / "nested").rename(self.root / "detached")
        (self.root / "nested").mkdir(mode=0o700)
        self.put("nested/b", b"unrelated canonical bytes")

    def test_canonical_parent_replacement_cannot_publish_complete(self):
        def change(point):
            if point == "before:complete":
                self.swap_nested_parent()

        with self.assertRaisesRegex(MillefeuilleContractError, "parent"):
            self.publish(fault=change)
        self.assertEqual(
            (self.root / "nested/b").read_bytes(), b"unrelated canonical bytes"
        )
        events = [
            json.loads(line)
            for line in (self.root / "txn/journal.jsonl").read_text().splitlines()
        ]
        self.assertNotIn("complete", [x["event"] for x in events])

    def test_canonical_parent_replacement_cannot_restore_into_detached_parent(self):
        self.publish()

        def change(point):
            if point == "rollback-before:1":
                self.swap_nested_parent()

        with self.assertRaisesRegex(MillefeuilleContractError, "parent"):
            self.rollback(fault=change)
        self.assertEqual(
            (self.root / "nested/b").read_bytes(), b"unrelated canonical bytes"
        )
        self.assertEqual(
            (self.root / "detached/b").read_bytes(), self.payloads["nested/b"]
        )
        events = [
            json.loads(line)
            for line in (self.root / "txn/journal.jsonl").read_text().splitlines()
        ]
        self.assertNotIn("rollback-complete", [x["event"] for x in events])

    def test_restored_target_drift_prevents_rollback_completion(self):
        self.publish()

        def change(point):
            if point == "rollback-after:0":
                self.put("nested/b", b"newer unrelated changes")

        with self.assertRaisesRegex(MillefeuilleContractError, "unrelated"):
            self.rollback(fault=change)
        events = [
            json.loads(line)
            for line in (self.root / "txn/journal.jsonl").read_text().splitlines()
        ]
        self.assertNotIn("rollback-complete", [x["event"] for x in events])

    def test_rollback_staging_drift_refuses_before_replacement(self):
        self.publish()

        def change(point):
            if point == "rollback-before:1":
                self.put("txn/rollback-1", b"wrong restoration")

        with self.assertRaisesRegex(MillefeuilleContractError, "staging"):
            self.rollback(fault=change)
        self.assertEqual(
            (self.root / "nested/b").read_bytes(), self.payloads["nested/b"]
        )

    def test_transaction_directory_substitution_prevents_terminal_success(self):
        def change(point):
            if point == "before:complete":
                (self.root / "txn").rename(self.root / "detached-txn")
                (self.root / "txn").mkdir(mode=0o700)

        with self.assertRaisesRegex(MillefeuilleContractError, "transaction"):
            self.publish(fault=change)
        events = [
            json.loads(line)
            for line in (self.root / "detached-txn/journal.jsonl")
            .read_text()
            .splitlines()
        ]
        self.assertNotIn("complete", [x["event"] for x in events])
