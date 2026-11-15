"""Synthetic reservation guards, not production publication proof."""

from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.cli.stages import run_stage_cli
from millefeuille.domain import secure_io
from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
from millefeuille.domain.classification import write_classification_from_evidence
from millefeuille.domain.classification_output_plan import (
    plan_classification_from_evidence,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.publication_journal import (
    execute_publication,
    reserve_publication,
    validate_rollback_postimages,
)
from tests.platform_capabilities import requires_secure_nofollow_writes
from tests.test_millefeuille_classification_batch import (
    _accept_run,
    _write_classification_evidence,
)
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)


class ClassificationPlanTests(unittest.TestCase):
    # Success needs secure acceptance writes. UnsupportedAcceptanceFixtureTests
    # separately verifies the real fixture refusal without filesystem changes.
    @requires_secure_nofollow_writes
    def test_all_ten_renderings_match_legacy_without_planner_writes(self):
        for adjudication in (False, True):
            with (
                self.subTest(adjudication=adjudication),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root, run = _prepare_fixture_run(tmp)
                _accept_run(
                    source_pack_root=root,
                    run_id=RUN_ID,
                    handoff_path=_write_handoff_jsonl(tmp),
                )
                evidence = Path(tmp) / "evidence.json"
                _write_classification_evidence(
                    evidence, adjudication_required=adjudication
                )
                from tests.test_persistence_guards import locked_evidence

                payload = json.loads(evidence.read_bytes())
                payload.update(locked_evidence(RUN_ID))
                evidence.write_text(json.dumps(payload))
                args = {
                    "evidence_path": evidence,
                    "source_pack_root": root,
                    "run_id": RUN_ID,
                    "paper_id": PAPER_ID,
                }
                before = {
                    str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()
                }
                plan = plan_classification_from_evidence(**args)
                self.assertEqual(len(plan), 10)
                self.assertEqual(
                    before,
                    {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()},
                )
                self.assertFalse((run / "classification").exists())
                write_classification_from_evidence(**args)
                for output in plan:
                    self.assertEqual((run / output.ref).read_bytes(), output.payload)
                for output in plan_classification_from_evidence(**args):
                    self.assertEqual(output.before_bytes, output.payload)
                    self.assertEqual(output.before_sha256, output.sha256)


class UnsupportedAcceptanceFixtureTests(unittest.TestCase):
    def assert_acceptance_refuses_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _run = _prepare_fixture_run(tmp)
            handoff = _write_handoff_jsonl(tmp)
            before = {
                p.relative_to(root): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }
            entries = sorted(p.relative_to(root) for p in root.rglob("*"))
            stdout, stderr = StringIO(), StringIO()
            code = run_stage_cli(
                [
                    "acceptance",
                    "--source-pack-root",
                    str(root),
                    "--paper-id",
                    PAPER_ID,
                    "--run-id",
                    RUN_ID,
                    "--handoff",
                    str(handoff),
                ],
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(code, 2)
            self.assertEqual(
                stderr.getvalue(),
                "millefeuille acceptance: transaction input tree requires "
                "no-follow filesystem reads on this platform\n",
            )
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                entries, sorted(p.relative_to(root) for p in root.rglob("*"))
            )
            self.assertEqual(
                before,
                {
                    p.relative_to(root): p.read_bytes()
                    for p in root.rglob("*")
                    if p.is_file()
                },
            )

    def test_native_missing_nofollow_refuses_fixture_before_writes(self):
        if secure_io._supports_no_follow():
            self.skipTest("native runtime supports O_NOFOLLOW; refusal tested below")
        self.assert_acceptance_refuses_without_writes()

    def test_missing_nofollow_refuses_fixture_before_writes(self):
        # Remove a capability only, never manufacture successful secure writes.
        with patch.object(secure_io, "_supports_no_follow", return_value=False):
            self.assert_acceptance_refuses_without_writes()


@unittest.skipUnless(os.name == "posix", "POSIX reservation contract")
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "a").write_bytes(b"old")
        (self.root / "a").chmod(0o600)
        self.plan = plan_exact_outputs(
            root=self.root, outputs={"a": b"new", "b": b"second"}
        )
        self.args = {
            "root": self.root,
            "outputs": self.plan,
            "approved_refs": {"a", "b"},
            "max_files": 2,
            "max_bytes": 100,
            "journal_ref": "intent.json",
            "max_journal_bytes": 10000,
        }

    def reserve(self, **changes):
        return reserve_publication(**(self.args | changes))

    def test_reservation_beforeimages_no_executor_no_replay(self):
        self.reserve()
        record = json.loads((self.root / "intent.json").read_bytes())
        self.assertEqual(record["state"], "reserved-no-executor")
        self.assertEqual(record["outputs"][0]["before_base64"], "b2xk")
        with self.assertRaises(MillefeuilleContractError):
            self.reserve()
        with self.assertRaises(MillefeuilleContractError):
            execute_publication()
        self.assertEqual((self.root / "a").read_bytes(), b"old")
        self.assertFalse((self.root / "b").exists())

    def test_preflight_all_limits_and_journal_locations(self):
        for changes in [
            {"max_files": 1},
            {"max_bytes": 1},
            {"max_journal_bytes": 1},
            {"approved_refs": {"a"}},
            {"journal_ref": "a"},
            {"journal_ref": "../escape"},
            {"journal_ref": "missing/intent"},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises((MillefeuilleContractError, OSError)),
            ):
                self.reserve(**changes)
        self.assertFalse((self.root / "intent.json").exists())

    def test_stale_beforeimage_and_forged_snapshot(self):
        for item in [
            replace(self.plan[0], before_uid=-1),
            replace(self.plan[0], sha256="0" * 64),
        ]:
            with self.assertRaises(MillefeuilleContractError):
                self.reserve(outputs=(item, self.plan[1]))
        (self.root / "a").write_bytes(b"drift")
        with self.assertRaises(MillefeuilleContractError):
            self.reserve()
        self.assertFalse((self.root / "intent.json").exists())

    def test_unsafe_ancestor_pathescape_and_nonregular(self):
        (self.root / "link").symlink_to(self.root, target_is_directory=True)
        for ref in ["../outside", "link/x", "a/child"]:
            with (
                self.subTest(ref=ref),
                self.assertRaises((MillefeuilleContractError, OSError)),
            ):
                self.reserve(
                    outputs=(replace(self.plan[1], ref=ref),), approved_refs={ref}
                )
        (self.root / "b").mkdir()
        with self.assertRaises(MillefeuilleContractError):
            self.reserve()

    def test_fsync_uncertainty_preserves_journal_and_blocks_replay(self):
        with (
            patch(
                "millefeuille.domain.secure_io.os.fsync",
                side_effect=OSError("injected fsync"),
            ),
            self.assertRaises(MillefeuilleContractError),
        ):
            self.reserve()
        self.assertTrue((self.root / "intent.json").exists())
        with self.assertRaises(MillefeuilleContractError):
            self.reserve()
        self.assertEqual((self.root / "a").read_bytes(), b"old")

    def test_fault_before_reservation_no_effect(self):
        with (
            patch(
                "millefeuille.domain.publication_journal.write_new_text_no_follow",
                side_effect=OSError("before intent"),
            ),
            self.assertRaises(OSError),
        ):
            self.reserve()
        self.assertFalse((self.root / "intent.json").exists())

    def test_partial_postimages_and_unrelated_changes_refused(self):
        (self.root / "a").write_bytes(b"new")
        with self.assertRaises(MillefeuilleContractError):
            validate_rollback_postimages(root=self.root, outputs=self.plan)
        (self.root / "b").write_bytes(b"second")
        self.assertEqual(
            validate_rollback_postimages(root=self.root, outputs=self.plan), ("a", "b")
        )
        (self.root / "b").write_bytes(b"other!")
        with self.assertRaises(MillefeuilleContractError):
            validate_rollback_postimages(root=self.root, outputs=self.plan)

    def test_unsafe_workspace_and_overlapping_refs(self):
        with self.assertRaises(MillefeuilleContractError):
            self.reserve(
                outputs=(self.plan[0], replace(self.plan[1], ref="a/child")),
                approved_refs={"a", "a/child"},
            )
        self.root.chmod(0o755)
        with self.assertRaises(MillefeuilleContractError):
            self.reserve()
