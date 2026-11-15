"""Real public-fixture semantic plans on the approved scratch-only seam."""

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from millefeuille.domain.acceptance import _build_acceptance_summary
from millefeuille.domain.acceptance_output_plan import json_bytes
from millefeuille.domain.classification_model import (
    validate_classification_model_output,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.semantic_materials import build_material_binding
from millefeuille.domain.semantic_output_plan import plan_semantic_acceptance
from millefeuille.domain.semantic_publication_orchestrator import (
    capture_bound_manifest,
    namespace_binding,
    plan_identity,
)
from millefeuille.domain.stage_runtime import resolve_run_artifacts
from tests import test_millefeuille_classification_model as classification_tests
from tests import test_millefeuille_semantic_acceptance as acceptance_tests
from tests import test_w3_inverse_proofs as inverse_tests
from tests.test_millefeuille_stage_cli import (
    PAPER_ID,
    RUN_ID,
    _prepare_fixture_run,
    _write_handoff_jsonl,
)


@unittest.skipUnless(
    os.name == "posix" and os.geteuid() != 0, "normal UID POSIX required"
)
class RealSemanticBindingTests(unittest.TestCase):
    publish = inverse_tests.W3InverseTests.publish
    rollback = inverse_tests.W3InverseTests.rollback

    def snapshot(self):
        return {
            str(p.relative_to(self.pack)): p.read_bytes()
            for p in self.pack.rglob("*")
            if p.is_file()
        }

    tearDown = inverse_tests.W3InverseTests.tearDown

    def setUp(self):
        inverse_tests.W3InverseTests.setUp(self)
        self.fixture_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture_tmp.cleanup)
        self.pack, self.root = _prepare_fixture_run(self.fixture_tmp.name)
        self.resolved = resolve_run_artifacts(
            source_pack_root=self.pack, run_id=RUN_ID, paper_id=PAPER_ID
        )
        self.summary = _build_acceptance_summary(
            resolved=self.resolved,
            handoff_path=_write_handoff_jsonl(self.fixture_tmp.name),
            duplicate_scan_path=None,
        )
        self.decision, self.evidence = (
            acceptance_tests.SemanticAcceptanceTests().fixture(self.root)
        )
        self.decision.update(
            paper_id=PAPER_ID,
            run_id=RUN_ID,
            source_hash=self.resolved.source_hash,
            material_binding=build_material_binding(self.resolved),
            structural_sha256=digest(json_bytes(self.summary.to_dict())),
            inventory_sha256=digest(
                json.dumps({"evidence": self.evidence}, sort_keys=True).encode()
            ),
        )
        (self.root / "classification/decision-records").mkdir(parents=True)
        (self.root / "reports").mkdir(exist_ok=True)
        for p in [self.root, *self.root.rglob("*")]:
            p.chmod(0o700 if p.is_dir() else 0o600)
        self.plan = self.render()
        self.assertEqual(len(self.plan), 6)
        roots = {"run": self.root, "source-pack": self.resolved.source_pack_dir}
        paths = [
            roots[f["root"]] / f["ref"]
            for f in self.decision["material_binding"]["files"]
        ]
        self.decision_path = self.root / "review-input.json"
        self.decision_path.write_bytes(json_bytes(self.decision))
        self.binding = capture_bound_manifest(sorted(set(paths + [self.decision_path])))
        self.scope.update(
            root=str(self.root),
            plan_identity=plan_identity(self.plan),
            input_binding=self.binding,
            code_binding=self.binding,
            approved_refs=[p.ref for p in self.plan],
            namespace_binding=namespace_binding(self.root, [p.ref for p in self.plan]),
            limits={
                "max_files": 6,
                "max_bytes": 1048576,
                "max_aux_paths": 16,
                "max_aux_bytes": 2097152,
                "max_journal_bytes": 524288,
            },
        )

    def render(self, decision=None):
        return plan_semantic_acceptance(
            resolved=self.resolved,
            summary=self.summary,
            decision=self.decision if decision is None else decision,
            required_evidence=self.evidence,
        )

    def test_real_decision_fields_resealed_outer_refuse_before_write(self):
        for field, value in [
            (
                "reviewer",
                {
                    "identity": "another-synthetic-reviewer",
                    "method": "all-material-claims",
                },
            ),
            ("outcome", "needs-review"),
            ("source_hash", "wrong"),
            ("structural_sha256", "sha256:" + "0" * 64),
            ("inventory_sha256", "sha256:" + "0" * 64),
        ]:
            with self.subTest(field=field):
                changed = deepcopy(self.decision)
                changed[field] = value
                self.decision_path.write_bytes(json_bytes(changed))
                before = self.snapshot()
                if field != "reviewer":
                    with self.assertRaises(Error):
                        self.render(changed)
                else:
                    self.assertNotEqual(
                        plan_identity(self.render(changed)), plan_identity(self.plan)
                    )
                # publish() reseals outer hash; original inner manifest is stale.
                with patch(
                    "millefeuille.domain.semantic_publication_orchestrator.publish_snapshot"
                ) as first_write:
                    with self.assertRaisesRegex(Error, "drift|changed"):
                        self.publish()
                    first_write.assert_not_called()
                self.assertEqual(before, self.snapshot())
                self.decision_path.write_bytes(json_bytes(self.decision))

    def test_real_selected_material_change_refuses_before_write(self):
        f = next(
            f
            for f in self.decision["material_binding"]["files"]
            if f["root"] == "source-pack" and f["ref"].endswith(".md")
        )
        path = self.resolved.source_pack_dir / f["ref"]
        path.write_bytes(path.read_bytes() + b" changed synthetic source")
        before = self.snapshot()
        with self.assertRaises(Error):
            self.render()
        with patch(
            "millefeuille.domain.semantic_publication_orchestrator.publish_snapshot"
        ) as first_write:
            with self.assertRaises(Error):
                self.publish()
            first_write.assert_not_called()
        self.assertEqual(before, self.snapshot())

    def test_each_actual_output_changed_under_resealed_outer_refuses(self):
        original = self.plan
        for i, item in enumerate(original):
            with self.subTest(ref=item.ref):
                payload = item.payload + b" "
                self.plan = (
                    original[:i]
                    + (replace(item, payload=payload, sha256=digest(payload)),)
                    + original[i + 1 :]
                )
                before = self.snapshot()
                with patch(
                    "millefeuille.domain.semantic_publication_orchestrator.publish_snapshot"
                ) as first_write:
                    with self.assertRaisesRegex(Error, "binding drift"):
                        self.publish()
                    first_write.assert_not_called()
                self.assertEqual(before, self.snapshot())
        self.plan = original

    def test_actual_six_outputs_separate_process_readback_and_rollback(self):
        before = self.snapshot()
        receipt = self.publish()
        code = """import json,sys
from millefeuille.domain.semantic_publication_orchestrator import (
 verify_publication_readback,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
r,s,fail=json.loads(sys.stdin.read())
try:
 verify_publication_readback(receipt=r, approved_scope=s)
except MillefeuilleContractError:
 assert fail
else:
 assert not fail
"""

        def check(fail):
            result = subprocess.run(
                [sys.executable, "-B", "-c", code],
                input=json.dumps([receipt, self.scope, fail]),
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        check(False)
        for item in self.plan:
            with self.subTest(ref=item.ref):
                path = self.root / item.ref
                self.assertEqual(path.read_bytes(), item.payload)
                path.write_bytes(item.payload + b" changed")
                check(True)
                path.write_bytes(item.payload)
        self.rollback()
        after = self.snapshot()
        for ref, identity in before.items():
            self.assertEqual(after[ref], identity)
        for item in self.plan:
            if item.before_bytes is None:
                self.assertFalse((self.root / item.ref).exists())
        self.assertTrue(
            any("transaction" in ref for ref in after.keys() - before.keys())
        )


class RealClassificationBindingTests(unittest.TestCase):
    def test_real_taxonomy_and_source_output_axes_are_pure(self):
        fixture = classification_tests.ClassificationModelTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from millefeuille.clients.zotero_client import ZoteroClient

        with ExitStack() as stack:
            spies = [
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
            plan = fixture.plan()
            before = {
                str(p): p.read_bytes() for p in fixture.root.rglob("*") if p.is_file()
            }
            validate_classification_model_output(fixture.output(plan), plan=plan)
            for field in ("source_hash", "taxonomy_version", "taxonomy_lock_identity"):
                with self.subTest(field=field):
                    output = fixture.output(plan)
                    output[field] = "wrong"
                    with self.assertRaises(Error):
                        validate_classification_model_output(output, plan=plan)
            for field in ("taxonomy_version", "content_identity"):
                with self.subTest(plan_field=field):
                    original = plan.taxonomy_lock[field]
                    plan.taxonomy_lock[field] = "wrong"
                    with self.assertRaises(Error):
                        validate_classification_model_output(
                            fixture.output(plan), plan=plan
                        )
                    plan.taxonomy_lock[field] = original
            self.assertEqual(
                before,
                {
                    str(p): p.read_bytes()
                    for p in fixture.root.rglob("*")
                    if p.is_file()
                },
            )
            from millefeuille.domain.taxonomy import seal_taxonomy_registry

            original_lock = deepcopy(plan.taxonomy_lock)
            for axis in ("taxonomy_version", "taxonomy_hash"):
                registry = deepcopy(fixture.registry)
                registry.pop("content_identity")
                if axis == "taxonomy_version":
                    registry["taxonomy_version"] = "synthetic-v2"
                else:
                    registry["entries"][1]["definition"] = "Changed synthetic meaning"
                registry = seal_taxonomy_registry(registry)
                replacement = fixture.write_lock(registry)
                plan.taxonomy_lock.clear()
                plan.taxonomy_lock.update(replacement)
                changed_before = {
                    str(p): p.read_bytes()
                    for p in fixture.root.rglob("*")
                    if p.is_file()
                }
                with self.subTest(resealed_axis=axis):
                    with self.assertRaisesRegex(Error, "changed after input binding"):
                        validate_classification_model_output(
                            fixture.output(plan), plan=plan
                        )
                    self.assertEqual(
                        changed_before,
                        {
                            str(p): p.read_bytes()
                            for p in fixture.root.rglob("*")
                            if p.is_file()
                        },
                    )
                plan.taxonomy_lock.clear()
                plan.taxonomy_lock.update(original_lock)
            for spy in spies:
                spy.assert_not_called()
