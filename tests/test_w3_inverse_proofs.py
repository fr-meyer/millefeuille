"""Synthetic W3 inverses, not genuine guard or product qualification."""

from copy import deepcopy
from dataclasses import replace
import json
import os
import unittest
from unittest.mock import patch

from millefeuille.domain.acceptance_output_plan import json_bytes, plan_exact_outputs
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.semantic_publication_orchestrator import (
    namespace_binding,
    verify_publication_readback,
)
from tests import test_semantic_publication_orchestrator as publication_tests


@unittest.skipUnless(
    os.name == "posix" and os.geteuid() != 0, "normal UID POSIX required"
)
class W3InverseTests(unittest.TestCase):
    # Reuse the approved synthetic seam, not inherited test discovery or R6.
    def setUp(self):
        publication_tests.PublicationTests.setUp(self)
        from contextlib import ExitStack

        from millefeuille.clients.zotero_client import ZoteroClient

        stack = ExitStack()
        self.addCleanup(stack.close)
        self.spies = [
            stack.enter_context(patch.object(ZoteroClient, name))
            for name in (
                "create_notes_batch",
                "delete_notes",
                "add_tag",
                "set_tags",
                "remove_tag",
                "_require_write_client",
            )
        ]

    def tearDown(self):
        for spy in self.spies:
            spy.assert_not_called()

    publish = publication_tests.PublicationTests.publish
    rollback = publication_tests.PublicationTests.rollback

    def snapshot(self):
        return {
            p.relative_to(self.root).as_posix(): digest(p.read_bytes())
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def test_resealed_outer_input_changes_keep_stale_inner_binding(self):
        axes = (
            "accepted_verdict",
            "source_hash",
            "decision_hash",
            "taxonomy_version",
            "taxonomy_hash",
            "selected_output",
        )
        for axis in axes:
            with self.subTest(axis=axis):
                # Names are synthetic payload axes, not real semantic validation.
                (self.root / "input").write_bytes(json_bytes({axis: "changed"}))
                before = self.snapshot()
                with patch(
                    "millefeuille.domain.semantic_publication_orchestrator."
                    "publish_snapshot"
                ) as effect:
                    with self.assertRaisesRegex(Error, "manifest drift"):
                        self.publish()  # helper reseals outer scope hash
                    effect.assert_not_called()
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.root / "transaction").exists())

    def test_resealed_outer_plan_change_refuses_before_first_mutation(self):
        before = self.snapshot()
        self.plan = (replace(self.plan[0], payload=b"changed"),)
        with patch(
            "millefeuille.domain.semantic_publication_orchestrator.publish_snapshot"
        ) as effect:
            with self.assertRaisesRegex(Error, "plan binding drift"):
                self.publish()
            effect.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def six_outputs(self):
        refs = (
            "acceptance.json",
            "acceptance.md",
            "decision.json",
            "inventory.json",
            "manifest.json",
            "index.json",
        )
        before = {ref: ("old:" + ref).encode() for ref in refs}
        for ref, data in before.items():
            (self.root / ref).write_bytes(data)
            (self.root / ref).chmod(0o600)
        self.plan = plan_exact_outputs(
            root=self.root, outputs={ref: ("new:" + ref).encode() for ref in refs}
        )
        from millefeuille.domain.semantic_publication_orchestrator import plan_identity

        refs = tuple(x.ref for x in self.plan)
        self.scope.update(
            plan_identity=plan_identity(self.plan),
            approved_refs=list(refs),
            namespace_binding=namespace_binding(self.root, list(refs)),
        )
        self.scope["limits"].update(
            max_files=6,
            max_bytes=65536,
            max_aux_paths=14,
            max_aux_bytes=1048576,
            max_journal_bytes=262144,
        )
        return refs, before

    def test_six_independent_output_identity_readback_inverses(self):
        refs, _ = self.six_outputs()
        receipt = self.publish()
        self.assertEqual(
            verify_publication_readback(receipt=receipt, approved_scope=self.scope)[
                "status"
            ],
            "pass",
        )
        for ref in refs:
            with self.subTest(ref=ref):
                target = self.root / ref
                original = target.read_bytes()
                target.write_bytes(b"independent-drift")
                observed = namespace_binding(self.root, refs)["outputs"]
                expected = dict(zip(refs, receipt["posts"], strict=True))
                self.assertEqual([r for r in refs if observed[r] != expected[r]], [ref])
                before = self.snapshot()
                with self.assertRaisesRegex(Error, "independent readback drift"):
                    verify_publication_readback(
                        receipt=deepcopy(receipt), approved_scope=deepcopy(self.scope)
                    )
                self.assertEqual(self.snapshot(), before)
                target.write_bytes(original)

    def test_foreign_transaction_and_changed_beforeimage_refuse(self):
        self.publish()
        journal = self.root / "transaction/journal.jsonl"
        original = journal.read_bytes()
        for axis in ("foreign-transaction", "beforeimage"):
            with self.subTest(axis=axis):
                rows = [json.loads(line) for line in original.splitlines()]
                if axis == "foreign-transaction":
                    rows[0]["transaction_ref"] = "foreign"
                else:
                    rows[0]["outputs"][0]["before_base64"] = "dGFtcGVyZWQ="
                journal.write_bytes(b"".join(json_bytes(row) for row in rows))
                before = self.snapshot()
                with self.assertRaises(Error):
                    self.rollback()
                self.assertEqual(self.snapshot(), before)

    def test_stale_resealed_rollback_binding_refuses(self):
        from millefeuille.domain.semantic_publication_orchestrator import (
            rollback_binding,
            rollback_reviewed_plan,
        )

        self.publish()
        approval = {
            "operation": "rollback",
            "approval_identity": "synthetic",
            "binding": rollback_binding(self.scope),
        }
        approval["binding"]["publication_scope_identity"] = "stale"
        before = self.snapshot()
        with self.assertRaisesRegex(Error, "rollback approval"):
            rollback_reviewed_plan(
                approved_scope=self.scope,
                rollback_approval=approval,
                approved_rollback_identity=digest(json_bytes(approval)),
            )
        self.assertEqual(self.snapshot(), before)

    def test_valid_six_output_rollback_exact_bytes_retains_journal(self):
        refs, before = self.six_outputs()
        self.publish()
        journal = self.root / "transaction/journal.jsonl"
        evidence = journal.read_bytes()
        self.rollback()
        self.assertEqual({r: (self.root / r).read_bytes() for r in refs}, before)
        self.assertTrue(journal.read_bytes().startswith(evidence))
        self.assertGreater(len(journal.read_bytes()), len(evidence))

    def test_actual_classification_uncertainty_and_ambiguity_no_mutations(self):
        from millefeuille.domain.classification_model import (
            validate_classification_model_output,
        )
        from tests.test_millefeuille_classification_model import (
            ClassificationModelTests,
        )

        fixture = ClassificationModelTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        plan = fixture.plan()
        before = {
            str(p): digest(p.read_bytes())
            for p in fixture.root.rglob("*")
            if p.is_file()
        }
        for axis in ("low-confidence", "taxonomy-gap", "self-alternative"):
            with self.subTest(axis=axis):
                output = fixture.output(plan)
                if axis == "self-alternative":
                    output["strongest_rejected_entry_id"] = output["primary_entry_id"]
                elif axis == "low-confidence":
                    output["confidence"] = "low"
                else:
                    output["taxonomy_gap"] = True
                with self.assertRaises(Error):
                    validate_classification_model_output(output, plan=plan)
                if axis != "self-alternative":
                    output["review_reasons"] = ["Synthetic uncertainty review required"]
                    self.assertEqual(
                        validate_classification_model_output(output, plan=plan)[
                            "review_reasons"
                        ],
                        output["review_reasons"],
                    )
                self.assertEqual(
                    before,
                    {
                        str(p): digest(p.read_bytes())
                        for p in fixture.root.rglob("*")
                        if p.is_file()
                    },
                )
