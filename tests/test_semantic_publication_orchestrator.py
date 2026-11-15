"""Synthetic normal-UID entrypoint crash/rollback and scope regressions."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from millefeuille.domain.acceptance_output_plan import json_bytes, plan_exact_outputs
from millefeuille.domain.exclusive_publication import CONTRACT
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.semantic_publication_orchestrator import (
    capture_bound_manifest,
    namespace_binding,
    plan_identity,
    publish_reviewed_plan,
    rollback_binding,
    rollback_reviewed_plan,
    verify_publication_readback,
)


@unittest.skipUnless(
    os.name == "posix" and os.geteuid() != 0, "normal UID POSIX required"
)
class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "out"
        self.target.write_bytes(b"old")
        self.target.chmod(0o600)
        self.plan = plan_exact_outputs(root=self.root, outputs={"out": b"new"})
        binding_file = self.root / "input"
        binding_file.write_bytes(b"synthetic")
        self.binding = capture_bound_manifest([binding_file])
        self.scope = {
            "schema": "millefeuille-reviewed-publication-scope/v1",
            "approval_identity": "synthetic-owner",
            "approved": True,
            "root": str(self.root),
            "journal": "transaction",
            "plan_identity": plan_identity(self.plan),
            "input_binding": self.binding,
            "code_binding": self.binding,
            "namespace_binding": namespace_binding(self.root, ["out"]),
            "approved_refs": ["out"],
            "namespace_contract": CONTRACT,
            "uses": 1,
            "limits": {
                "max_files": 1,
                "max_bytes": 64,
                "max_aux_paths": 4,
                "max_aux_bytes": 65536,
                "max_journal_bytes": 32768,
            },
        }

    def publish(self, **kw):
        return publish_reviewed_plan(
            exact_plan=self.plan,
            approved_scope=self.scope,
            namespace=self.root,
            journal="transaction",
            input_binding=self.binding,
            approved_scope_identity=digest(json_bytes(self.scope)),
            code_binding=self.binding,
            **kw,
        )

    def rollback(self):
        approval = {
            "operation": "rollback",
            "approval_identity": "synthetic",
            "binding": rollback_binding(self.scope),
        }
        return rollback_reviewed_plan(
            approved_scope=self.scope,
            rollback_approval=approval,
            approved_rollback_identity=digest(json_bytes(approval)),
        )

    def test_publish_independent_process_readback_and_rollback(self):
        receipt = self.publish()
        code = """import json,sys
from millefeuille.domain.semantic_publication_orchestrator import (
 verify_publication_readback,
)
r,s=json.loads(sys.stdin.read())
assert verify_publication_readback(receipt=r,approved_scope=s)['status']=='pass'
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            input=json.dumps([receipt, self.scope]),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.rollback()
        self.assertEqual(self.target.read_bytes(), b"old")
        with self.assertRaises(Error):
            self.publish()

    def test_drift_and_reservations_before_writes(self):
        for key, value in [
            ("code_binding", "drift"),
            ("uses", 2),
            ("approved_refs", ["other"]),
        ]:
            original = deepcopy(self.scope)
            self.scope[key] = value
            with self.assertRaises(Error):
                self.publish()
            self.assertFalse((self.root / "transaction").exists())
            self.scope = original
        self.scope["limits"]["max_aux_bytes"] = 1
        with self.assertRaises(Error):
            self.publish()
        self.assertFalse((self.root / "transaction").exists())

    def test_entrypoint_hard_crash_uncertain_window_refuses_recovery(self):
        code = """import json,os,sys
from pathlib import Path
from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
from millefeuille.domain.semantic_publication_orchestrator import publish_reviewed_plan
from millefeuille.domain.semantic_acceptance import digest
from millefeuille.domain.acceptance_output_plan import json_bytes
s=json.loads(sys.stdin.read()); r=Path(s['root'])
p=plan_exact_outputs(root=r,outputs={'out':b'new'})
def fault(label):
 if label=='effect:0': os._exit(71)
publish_reviewed_plan(exact_plan=p,approved_scope=s,namespace=r,journal='transaction',input_binding=s['input_binding'],code_binding=s['code_binding'],approved_scope_identity=digest(json_bytes(s)),fault=fault)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            input=json.dumps(self.scope),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 71, result.stderr)
        self.assertEqual(self.target.read_bytes(), b"new")
        with self.assertRaisesRegex(Error, "uncertain"):
            self.rollback()
        self.assertTrue((self.root / "transaction/journal.jsonl").exists())

    def test_fault_before_intent_can_roll_back(self):
        def fault(label):
            if label == "before:0":
                raise RuntimeError("synthetic crash")

        with self.assertRaises(RuntimeError):
            self.publish(fault=fault)
        self.rollback()
        self.assertEqual(self.target.read_bytes(), b"old")

    def test_readback_detects_change(self):
        receipt = self.publish()
        self.target.write_bytes(b"unrelated")
        with self.assertRaises(Error):
            verify_publication_readback(receipt=receipt, approved_scope=self.scope)

    def test_rehashed_input_drift_and_rollback_approval_refuse(self):
        (self.root / "input").write_bytes(b"tampered")
        with self.assertRaisesRegex(Error, "manifest drift"):
            self.publish()
        self.assertFalse((self.root / "transaction").exists())
        self.binding = capture_bound_manifest([self.root / "input"])
        self.scope.update(input_binding=self.binding, code_binding=self.binding)
        self.publish()
        approval = {
            "operation": "rollback",
            "approval_identity": "synthetic",
            "binding": rollback_binding(self.scope),
        }
        approved_hash = digest(json_bytes(approval))
        approval["binding"]["publication_scope_identity"] = "tampered"
        with self.assertRaisesRegex(Error, "rollback approval"):
            rollback_reviewed_plan(
                approved_scope=self.scope,
                rollback_approval=approval,
                approved_rollback_identity=approved_hash,
            )
        self.assertEqual(self.target.read_bytes(), b"new")

    def test_real_semantic_classification_release_roundtrip(self):
        import tomllib

        from millefeuille.domain.acceptance import _build_acceptance_summary
        from millefeuille.domain.classification_output_plan import (
            plan_classification_from_evidence,
        )
        from millefeuille.domain.release_preflight import (
            no_writeback_binding,
            write_release_candidate_preflight,
        )
        from millefeuille.domain.semantic_materials import build_material_binding
        from millefeuille.domain.semantic_output_plan import plan_semantic_acceptance
        from millefeuille.domain.stage_runtime import resolve_run_artifacts
        from tests.test_millefeuille_semantic_acceptance import SemanticAcceptanceTests
        from tests.test_millefeuille_stage_cli import (
            PAPER_ID,
            RUN_ID,
            _prepare_fixture_run,
            _write_handoff_jsonl,
        )
        from tests.test_persistence_guards import locked_evidence

        with tempfile.TemporaryDirectory() as tmp:
            root, run = _prepare_fixture_run(tmp)
            resolved = resolve_run_artifacts(
                source_pack_root=root, run_id=RUN_ID, paper_id=PAPER_ID
            )
            summary = _build_acceptance_summary(
                resolved=resolved,
                handoff_path=_write_handoff_jsonl(tmp),
                duplicate_scan_path=None,
            )
            decision, evidence = SemanticAcceptanceTests().fixture(run)
            decision.update(
                paper_id=PAPER_ID,
                run_id=RUN_ID,
                source_hash=resolved.source_hash,
                material_binding=build_material_binding(resolved),
                structural_sha256=digest(json_bytes(summary.to_dict())),
                inventory_sha256=digest(
                    json.dumps({"evidence": evidence}, sort_keys=True).encode()
                ),
            )
            (run / "classification/decision-records").mkdir(parents=True)
            (run / "reports").mkdir(exist_ok=True)
            # Synthetic fixture preparation only, never runtime owner/mode repair.
            for path in [run, *run.rglob("*")]:
                path.chmod(0o700 if path.is_dir() else 0o600)
            plan = plan_semantic_acceptance(
                resolved=resolved,
                summary=summary,
                decision=decision,
                required_evidence=evidence,
            )
            # Finite actual scientific input closure, not this test file.
            material = build_material_binding(resolved)
            roots = {"run": run, "source-pack": resolved.source_pack_dir}
            inputs = sorted(
                {(roots[f["root"]] / f["ref"]).absolute() for f in material["files"]}
            )
            input_binding = capture_bound_manifest(inputs)
            # Record the locally executed package implementation closure.
            import sys

            code_paths = sorted(
                {
                    Path(module.__file__).absolute()
                    for name, module in tuple(sys.modules.items())
                    if name.startswith("millefeuille.")
                    and getattr(module, "__file__", None)
                    and Path(module.__file__).suffix == ".py"
                }
            )
            from millefeuille.domain.semantic_publication_orchestrator import (
                prepare_workload_admission,
            )
            from scripts.millefeuille_offline_closure import reservation

            ia = prepare_workload_admission(
                inputs,
                roots=[Path(tmp)],
                context="test-inputs",
                reservations=reservation(inputs),
            )
            ca = prepare_workload_admission(
                code_paths,
                roots=[Path(__file__).absolute().parents[1]],
                context="test-code",
                reservations=reservation(code_paths),
            )
            input_binding = capture_bound_manifest(inputs, admission=ia)
            binding = capture_bound_manifest(code_paths, admission=ca)

            def publish(plan, journal):
                refs = [x.ref for x in plan]
                scope = {
                    **self.scope,
                    "schema": "millefeuille-reviewed-publication-scope/v2",
                    "input_admission": ia.binding(),
                    "code_admission": ca.binding(),
                    "root": str(run),
                    "journal": journal,
                    "plan_identity": plan_identity(plan),
                    "approved_refs": refs,
                    "namespace_binding": namespace_binding(run, refs),
                    "input_binding": input_binding,
                    "code_binding": binding,
                    "limits": {
                        "max_files": 16,
                        "max_bytes": 1048576,
                        "max_aux_paths": 34,
                        "max_aux_bytes": 2097152,
                        "max_journal_bytes": 524288,
                    },
                }
                receipt = publish_reviewed_plan(
                    exact_plan=plan,
                    input_admission=ia,
                    code_admission=ca,
                    approved_scope=scope,
                    namespace=run,
                    journal=journal,
                    input_binding=input_binding,
                    code_binding=binding,
                    approved_scope_identity=digest(json_bytes(scope)),
                )
                self.assertEqual(
                    verify_publication_readback(
                        receipt=receipt,
                        approved_scope=scope,
                        input_admission=ia,
                        code_admission=ca,
                    )["status"],
                    "pass",
                )

            publish(plan, "acceptance-transaction")
            evidence_path = Path(tmp) / "classification.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        **locked_evidence(RUN_ID),
                        "schema_version": (
                            "millefeuille-classification-fixture-evidence/v0.1"
                        ),
                        "confidence": "high",
                        "evidence_refs": ["cards/paper-card.json"],
                    }
                )
            )
            args = {"source_pack_root": root, "run_id": RUN_ID, "paper_id": PAPER_ID}
            publish(
                plan_classification_from_evidence(evidence_path=evidence_path, **args),
                "classification-transaction",
            )
            resolved = resolve_run_artifacts(**args)
            self.assertIn(
                "classify",
                [
                    x.name.value
                    for x in resolved.stage_manifest.stages
                    if x.status.value == "passed"
                ],
            )
            repo = Path(__file__).absolute().parents[1]
            version = tomllib.loads((repo / "pyproject.toml").read_text())["project"][
                "version"
            ]
            disposition = {
                "schema": "millefeuille-writeback-disposition/v2",
                "input_admission": ia.binding(),
                "code_admission": ca.binding(),
                "enabled": False,
                "disposition": "explicit_approved_no_writeback",
                "remote_operations": 0,
                "release_preflight_consumer_result": "pass",
                "approval_identity": "synthetic-owner",
                "code_manifest": binding,
                "input_manifest": input_binding,
                "binding": no_writeback_binding(
                    resolved=resolved, current_version=version, candidate_version=None
                ),
            }
            result = write_release_candidate_preflight(
                **args,
                repo_root=repo,
                writeback_disposition=disposition,
                input_admission=ia,
                code_admission=ca,
                approved_disposition_identity=digest(json_bytes(disposition)),
            )
            self.assertEqual(result.readiness, "ready-for-rc-review")
            with self.assertRaises(Error):
                write_release_candidate_preflight(
                    **args,
                    repo_root=repo,
                    writeback_disposition=disposition,
                    approved_disposition_identity="tampered",
                )


class ManifestCaptureBoundaryTests(unittest.TestCase):
    def test_64_success_65_refuses_before_metadata_or_read(self):
        from unittest.mock import patch

        from millefeuille.domain.secure_io import read_bytes_no_follow

        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / str(n) for n in range(65)]
            for path in paths:
                path.write_bytes(b"x")
            exact = capture_bound_manifest(paths[:64])
            self.assertEqual(len(exact["files"]), 64)
            original_stat = Path.lstat
            metadata = []

            def observed_stat(path, *args, **kwargs):
                metadata.append(path)
                return original_stat(path, *args, **kwargs)

            with (
                patch(
                    "millefeuille.domain.secure_io.read_bytes_no_follow",
                    wraps=read_bytes_no_follow,
                ) as reads,
                patch.object(Path, "lstat", observed_stat),
            ):
                with self.assertRaisesRegex(Error, "64"):
                    capture_bound_manifest(iter(paths))
                self.assertEqual(reads.call_count, 64)
                self.assertNotIn(paths[64], metadata)

    def test_oversize_and_symlink_refuse_before_read(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large"
            with path.open("wb") as file:
                file.truncate(2097153)
            link = Path(temp) / "link"
            link.symlink_to(path)
            for target in [path, link]:
                with patch(
                    "millefeuille.domain.secure_io.read_bytes_no_follow"
                ) as read:
                    with self.assertRaises(Error):
                        capture_bound_manifest([target])
                    read.assert_not_called()


class WorkloadAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = []
        for i in range(73):
            path = self.root / str(i)
            path.write_bytes(b"scoped fixture")
            self.paths.append(path)

    def admit(self, paths=None, **overrides):
        from millefeuille.domain.semantic_publication_orchestrator import (
            prepare_workload_admission,
        )
        from scripts.millefeuille_offline_closure import reservation

        paths = self.paths if paths is None else paths
        limits = reservation(paths)
        limits.update(overrides)
        return prepare_workload_admission(
            paths, roots=[self.root], context="independent-fixture", reservations=limits
        )

    def test_above64_success_reseal_and_legacy_unchanged(self):
        from millefeuille.domain.semantic_publication_orchestrator import (
            decode_workload_manifest,
            verify_bound_manifest,
        )

        admission = self.admit()
        manifest = capture_bound_manifest(self.paths, admission=admission)
        self.assertEqual(len(manifest["files"]), 73)
        self.assertEqual(
            decode_workload_manifest(json_bytes(manifest), admission=admission),
            manifest,
        )
        self.assertEqual(
            verify_bound_manifest(manifest, admission=admission),
            digest(json_bytes(manifest)),
        )
        with self.assertRaises(Error):
            capture_bound_manifest(self.paths)
        extra = self.root / "extra"
        extra.write_bytes(b"legitimate separately resealed inventory")
        changed = self.paths + [extra]
        second = self.admit(changed)
        self.assertEqual(
            len(capture_bound_manifest(changed, admission=second)["files"]), 74
        )
        with self.assertRaises(Error):
            capture_bound_manifest(changed, admission=admission)

    def test_unexpected_missing_duplicate_and_iterator_without_io(self):
        from unittest.mock import patch

        from millefeuille.domain.semantic_publication_orchestrator import (
            verify_bound_manifest,
        )

        admission = self.admit()
        manifest = admission.manifest()
        for paths in [
            self.paths + [self.root / "unexpected"],
            self.paths[:-1],
            self.paths[:-1] + self.paths[:1],
        ]:
            with (
                patch.object(
                    Path, "lstat", side_effect=AssertionError("no unexpected metadata")
                ),
                self.assertRaises(Error),
            ):
                capture_bound_manifest(paths, admission=admission)

        def untrusted():
            raise AssertionError("never execute untrusted iterator")
            yield None

        with self.assertRaises(Error):
            capture_bound_manifest(untrusted(), admission=admission)
        wrong = json.loads(json.dumps(manifest))
        wrong["files"][-1]["path"] = str(self.root / "unexpected")
        with (
            patch.object(
                Path, "lstat", side_effect=AssertionError("no unexpected metadata")
            ),
            self.assertRaises(Error),
        ):
            verify_bound_manifest(wrong, admission=admission)

    def test_resource_refusals_before_reads_or_json_allocation(self):
        from unittest.mock import patch

        from millefeuille.domain.semantic_publication_orchestrator import (
            decode_workload_manifest,
        )

        admission = self.admit()
        with patch(
            "millefeuille.domain.secure_io.read_bytes_no_follow",
            side_effect=AssertionError("no payload"),
        ):
            for limits in [
                {"read_bytes": 1},
                {"opens": 1},
                {"metadata_bytes": 1},
                {"memory_bytes": 1},
            ]:
                with self.assertRaises(Error):
                    self.admit(**limits)
        with (
            patch("json.loads", side_effect=AssertionError("no decode allocation")),
            self.assertRaises(Error),
        ):
            decode_workload_manifest(
                b"x" * (dict(admission.reservations)["metadata_bytes"] + 1),
                admission=admission,
            )
        # Prepare the finite reservation before mocking the shared time module.
        # The controller's sizing clock is not the product deadline under test.
        from millefeuille.domain.semantic_publication_orchestrator import (
            prepare_workload_admission,
        )

        limits = dict(admission.reservations, seconds=30)
        with (
            patch(
                "millefeuille.domain.semantic_publication_orchestrator.time.monotonic",
                side_effect=[0, 31],
            ),
            self.assertRaises(Error),
        ):
            prepare_workload_admission(
                self.paths,
                roots=[self.root],
                context="deadline-test",
                reservations=limits,
            )

    def test_identity_scope_symlink_and_hash_drift(self):
        from millefeuille.domain.semantic_publication_orchestrator import (
            prepare_workload_admission,
            verify_bound_manifest,
        )
        from scripts.millefeuille_offline_closure import reservation

        admission = self.admit()
        self.paths[0].write_bytes(b"changed")
        with self.assertRaises(Error):
            verify_bound_manifest(admission.manifest(), admission=admission)
        alias = self.root / "alias"
        alias.symlink_to(self.paths[1])
        with self.assertRaises(Error):
            self.admit([alias])
        with self.assertRaises(Error):
            prepare_workload_admission(
                self.paths,
                roots=[self.root / "other"],
                context="wrong-root",
                reservations=reservation(self.paths),
            )
        oversized = self.root / "oversized"
        with oversized.open("wb") as handle:
            handle.truncate(2097153)
        with self.assertRaises(Error):
            self.admit([oversized])
