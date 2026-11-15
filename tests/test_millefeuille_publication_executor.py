"""Deliberate refusal coverage, not successful transaction qualification."""

import os
from pathlib import Path
import tempfile
import unittest

from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.publication_journal import (
    execute_reserved_publication,
    reserve_publication,
    rollback_reserved_publication,
)


@unittest.skipUnless(os.name == "posix", "POSIX reservation contract")
class ExecutorBoundaryTests(unittest.TestCase):
    def test_explicit_execution_refuses_without_effects_or_false_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "a"
            target.write_bytes(b"original")
            target.chmod(0o600)
            inode = target.stat().st_ino
            with self.assertRaisesRegex(MillefeuilleContractError, "not qualified"):
                execute_reserved_publication(root=root)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(target.stat().st_ino, inode)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["a"])

    def test_consumed_reservation_survives_all_explicit_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = {
                "root": root,
                "outputs": plan_exact_outputs(root=root, outputs={"a": b"new"}),
                "approved_refs": {"a"},
                "journal_ref": "intent.json",
                "max_files": 1,
                "max_bytes": 100,
                "max_journal_bytes": 10000,
            }
            reserve_publication(**args)
            journal = (root / "intent.json").read_bytes()
            for operation in (
                execute_reserved_publication,
                rollback_reserved_publication,
            ):
                with self.assertRaises(MillefeuilleContractError):
                    operation(**args)
                self.assertEqual((root / "intent.json").read_bytes(), journal)
                self.assertFalse((root / "a").exists())
            with self.assertRaises((MillefeuilleContractError, OSError)):
                reserve_publication(**args)
            self.assertEqual((root / "intent.json").read_bytes(), journal)
