"""Local resource inverses, not genuine crash or guard qualification."""

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
from millefeuille.domain.evidence_source_adapter import (
    BudgetExhaustedError,
    ReadBudget,
    ScopedReadOnlyReader,
)
from millefeuille.domain.exclusive_publication import CONTRACT, publish_snapshot
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.semantic_publication_orchestrator import (
    WorkloadAdmission,
    decode_workload_manifest,
    prepare_workload_admission,
)


class RemainingResourceInverses(unittest.TestCase):
    def test_sticky_first_large_refusal_blocks_smaller_real_read(self):
        budget = ReadBudget(max_read_bytes=2)
        reader = ScopedReadOnlyReader(
            root="/synthetic",
            files={"x": {"identity": (0, 0, 0, 0, 0, 1)}},
            ancestors={},
        )
        with self.assertRaises(BudgetExhaustedError):
            budget.debit(read_bytes=3)
        with (
            patch("os.open") as opened,
            patch("os.read") as read,
            patch("pathlib.Path.lstat") as metadata,
            patch("os.write") as write,
            self.assertRaises(BudgetExhaustedError),
        ):
            reader.read("x", budget)
        for spy in (opened, read, metadata, write):
            spy.assert_not_called()
        self.assertEqual((budget.read_bytes, budget.opens, budget.scans), (0, 0, 0))
        self.assertTrue(budget.exhausted)

    def test_decode_exact_metadata_and_allocation_before_json(self):
        # Stateless decoder: smaller later calls are legitimate, not sticky.
        admission = WorkloadAdmission("synthetic", (), (), ())
        for axis, exact in [("metadata_bytes", 2), ("memory_bytes", 12)]:
            limits = {"metadata_bytes": 100, "memory_bytes": 100}
            limits[axis] = exact
            admitted = replace(admission, reservations=tuple(limits.items()))
            with (
                self.subTest(axis=axis),
                patch("json.loads", return_value={}) as loads,
                patch(
                    "millefeuille.domain.semantic_publication_orchestrator._verify_workload"
                ) as verify,
            ):
                self.assertEqual(
                    decode_workload_manifest(b"{}", admission=admitted), {}
                )
                loads.assert_called_once_with(b"{}")
                verify.assert_called_once_with({}, admitted)
                loads.reset_mock()
                verify.reset_mock()
                with self.assertRaises(Error):
                    decode_workload_manifest(b"{} ", admission=admitted)
                loads.assert_not_called()
                verify.assert_not_called()
                decode_workload_manifest(b"0", admission=admitted)
                loads.assert_called_once_with(b"0")

    def test_workload_exact_read_metadata_memory_enumeration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "x"
            target.write_bytes(b"abc")
            size = len(str(target).encode()) + 512
            base = {
                "read_bytes": 3,
                "opens": 1,
                "metadata_bytes": size,
                "memory_bytes": size * 6 + 9,
                "seconds": 1,
            }
            for axis in ("read_bytes", "opens", "metadata_bytes", "memory_bytes"):
                with self.subTest(axis=axis):
                    receipt = prepare_workload_admission(
                        [target], roots=[root], context="synthetic", reservations=base
                    )
                    self.assertEqual(len(receipt.entries), 1)
                    limits = dict(base)
                    paths = [target]
                    if axis == "opens":
                        paths.append(root / "never-stat")
                    else:
                        limits[axis] -= 1
                    with (
                        patch(
                            "millefeuille.domain.secure_io.read_bytes_no_follow"
                        ) as read,
                        patch("os.write") as write,
                        self.assertRaises(Error),
                    ):
                        prepare_workload_admission(
                            paths,
                            roots=[root],
                            context="synthetic",
                            reservations=limits,
                        )
                    read.assert_not_called()
                    write.assert_not_called()
            self.assertEqual(target.read_bytes(), b"abc")

    def test_workload_deadline_exact_then_over_before_metadata(self):
        limits = {
            "read_bytes": 3,
            "opens": 1,
            "metadata_bytes": 4096,
            "memory_bytes": 65536,
            "seconds": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x"
            target.write_bytes(b"abc")
            with patch(
                "millefeuille.domain.semantic_publication_orchestrator.time.monotonic",
                side_effect=[0, 1, 1],
            ):
                self.assertEqual(
                    len(
                        prepare_workload_admission(
                            [target], roots=[tmp], context="clock", reservations=limits
                        ).entries
                    ),
                    1,
                )
            with (
                patch(
                    "millefeuille.domain.semantic_publication_orchestrator.time.monotonic",
                    side_effect=[0, 1.001],
                ),
                patch("pathlib.Path.lstat") as metadata,
                patch("millefeuille.domain.secure_io.read_bytes_no_follow") as read,
                self.assertRaisesRegex(Error, "deadline"),
            ):
                prepare_workload_admission(
                    [target], roots=[tmp], context="clock", reservations=limits
                )
            metadata.assert_not_called()
            read.assert_not_called()

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "normal UID POSIX")
    def test_snapshot_exact_write_reservation_plus_one_no_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "x"
            target.write_bytes(b"old")
            target.chmod(0o600)
            outputs = plan_exact_outputs(root=root, outputs={"x": b"new"})
            kwargs = {
                "root": root,
                "outputs": outputs,
                "approved_refs": ["x"],
                "namespace_contract": CONTRACT,
                "max_files": 1,
                "max_bytes": 6,
                "max_aux_paths": 4,
                "max_aux_bytes": 65536,
                "max_journal_bytes": 32768,
            }
            with (
                patch("os.mkdir") as mkdir,
                patch("os.write") as write,
                patch("os.replace") as replaced,
                self.assertRaisesRegex(Error, "snapshot exceeds"),
            ):
                publish_snapshot(
                    **dict(kwargs, max_bytes=5), transaction_ref="rejected"
                )
            for spy in (mkdir, write, replaced):
                spy.assert_not_called()
            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse((root / "rejected").exists())
            publish_snapshot(**kwargs, transaction_ref="accepted")
            self.assertEqual(target.read_bytes(), b"new")

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "normal UID POSIX")
    def test_synchronous_prewrite_cancel_journal_no_replay(self):
        # Existing disjoint callback, pure exception, never actual death.
        for checkpoint in ("before:0", "intent:0"):
            with (
                self.subTest(checkpoint=checkpoint),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                target = root / "x"
                target.write_bytes(b"old")
                target.chmod(0o600)
                outputs = plan_exact_outputs(root=root, outputs={"x": b"new"})
                kwargs = {
                    "root": root,
                    "outputs": outputs,
                    "approved_refs": ["x"],
                    "transaction_ref": "txn",
                    "namespace_contract": CONTRACT,
                    "max_files": 1,
                    "max_bytes": 6,
                    "max_aux_paths": 4,
                    "max_aux_bytes": 65536,
                    "max_journal_bytes": 32768,
                }

                def cancel(label, checkpoint=checkpoint):
                    if label == checkpoint:
                        raise InterruptedError("synthetic cancellation")

                with self.assertRaises(InterruptedError):
                    publish_snapshot(**kwargs, fault=cancel)
                self.assertEqual(target.read_bytes(), b"old")
                journal = root / "txn/journal.jsonl"
                before = journal.read_bytes()
                events = [json.loads(line)["event"] for line in before.splitlines()]
                self.assertEqual(
                    events,
                    ["reserved", "intent"]
                    if checkpoint == "intent:0"
                    else ["reserved"],
                )
                with self.assertRaisesRegex(Error, "consumed"):
                    publish_snapshot(**kwargs)
                self.assertEqual(journal.read_bytes(), before)
                self.assertEqual(target.read_bytes(), b"old")
